from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, Tuple


def detect_hardware_device() -> torch.device:
    """
    Hardware-aware device detection:
    - Apple Silicon Mac: MPS (Metal Performance Shaders)
    - NVIDIA GPU: CUDA
    - CPU Fallback
    """
    if torch.backends.mps.is_available():
        return torch.device("mps")
    elif torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


class BetaNLLLoss(nn.Module):
    """
    Joint Beta-NLL Loss Function (Seitzer et al., ICLR 2022).
    Optimizes Expected Return (μ) and Aleatoric Variance (σ²) jointly.
    
    L_beta = 0.5 * (σ²)^beta * [ ((y - μ)^2 / σ²) + ln(σ²) ]
    
    Setting beta = 0.5 weights gradients by 1/σ rather than 1/σ², preventing
    the network from down-weighting difficult-to-fit samples.
    Setting beta = 0.0 recovers standard Gaussian NLL (mandatory baseline).
    """

    def __init__(self, beta: float = 0.5, eps: float = 1e-6) -> None:
        super().__init__()
        self.beta = beta
        self.eps = eps

    def forward(self, mu: torch.Tensor, var: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        var = torch.clamp(var, min=self.eps)
        
        # Loss computation
        sq_error = (target - mu) ** 2
        nll = 0.5 * (sq_error / var + torch.log(var))

        if self.beta > 0:
            weight = (var.detach()) ** self.beta
            loss = weight * nll
        else:
            loss = nll  # Standard Gaussian NLL baseline (beta=0.0)

        return torch.mean(loss)


class TemporalTransformerModel(nn.Module):
    """
    5-Head Multimodal Temporal Transformer Neural Network.
    
    Outputs:
    - Head 1: Direction Probability P(Up) -> BCE Loss
    - Head 2: Expected Return μ -> Beta-NLL Loss
    - Head 3: Expected Variance σ² -> Beta-NLL Loss
    - Head 4: Trade Success Probability P(TP before SL) -> BCE Loss
    - Head 5: Drawdown Risk Score -> Huber Loss
    
    Features Post-Hoc Explainability (SHAP / Integrated Gradients) on the shared representation.
    """

    def __init__(
        self,
        input_dim: int = 16,
        d_model: int = 64,
        nhead: int = 4,
        num_layers: int = 2,
        dim_feedforward: int = 128,
        dropout: float = 0.1,
        sequence_length: int = 30,
    ) -> None:
        super().__init__()
        self.input_dim = input_dim
        self.d_model = d_model
        self.sequence_length = sequence_length

        self.input_proj = nn.Linear(input_dim, d_model)
        self.pos_encoder = nn.Parameter(torch.zeros(1, sequence_length, d_model))

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            batch_first=True,
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

        # 5 Separate Output Heads
        self.head1_direction = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid()
        )

        self.head2_return_mu = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 1)
        )

        self.head3_variance_var = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Softplus()
        )
        self.variance_scale = 1e-3  # Scaler ensuring σ² matches decimal return variance scale (~0.0001 - 0.0025)

        self.head4_trade_success = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid()
        )

        self.head5_drawdown_risk = nn.Sequential(
            nn.Linear(d_model, 32),
            nn.ReLU(),
            nn.Linear(32, 1)
        )

    def extract_backbone_representation(self, x: torch.Tensor) -> torch.Tensor:
        """Extract shared Representation Vector for Post-Hoc Explainability (SHAP / IG)."""
        seq_len = x.size(1)
        out = self.input_proj(x) + self.pos_encoder[:, :seq_len, :]
        out = self.transformer_encoder(out)
        return out[:, -1, :]  # Final sequence token representation

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        """
        Forward pass yielding 5 outputs in matching return units:
        - prob_up: P(Up) [0.0, 1.0]
        - mu: Expected Return
        - var: Aleatoric Variance σ² (> 0, matched to return units)
        - trade_success_prob: P(TP hit before SL) [0.0, 1.0]
        - drawdown_risk: Expected Drawdown Score
        """
        rep = self.extract_backbone_representation(x)

        prob_up = self.head1_direction(rep)
        mu = self.head2_return_mu(rep)
        raw_var = self.head3_variance_var(rep)
        var = raw_var * self.variance_scale + 1e-5  # Matched variance units
        trade_success_prob = self.head4_trade_success(rep)
        drawdown_risk = self.head5_drawdown_risk(rep)

        return {
            "prob_up": prob_up,
            "mu": mu,
            "var": var,
            "trade_success_prob": trade_success_prob,
            "drawdown_risk": drawdown_risk,
            "shared_representation": rep,
        }

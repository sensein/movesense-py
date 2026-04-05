# State-of-the-Art Physics-Based ML for Physiological Signals

Research compilation for the movensense physio library. Models suitable for single-lead ECG + IMU from chest-worn sensor, PyTorch with MPS.

## 1. State-Space Models (Mamba/S4) for Biosignals

The most active research area for efficient sequence modeling of physiological data.

| Paper | arXiv | Date | Key Innovation |
|-------|-------|------|---------------|
| ECG-RAMBA: Zero-Shot ECG Generalization | 2512.23347 | Dec 2025 | Bidirectional Mamba with morphology-rhythm disentanglement |
| BioMamba: Spectro-Temporal Embedding | 2503.11741 | Mar 2025 | Bidirectional Mamba for ECG + EEG, spectro-temporal features |
| ECGMamba: Efficient ECG with BiSSM | 2406.10098 | Jun 2024 | Bidirectional SSM, lightweight, efficient classification |
| MambaCapsule: Transparent Cardiac Diagnosis | 2407.20893 | Jul 2024 | Mamba features + capsule networks for interpretability |
| Chimera: 2D State Space Models | 2406.04320 | Jun 2024 | Extends SSMs to multivariate time series |
| WildECG: Scaling with SSMs | 2309.15292 | Sep 2023 | Pre-trained SSM on 275K wearable ECG recordings |

**Best for us**: BioMamba or ECGMamba — both are bidirectional SSMs designed for biosignals, efficient enough for MPS.

## 2. Foundation Models for ECG/Wearable

Pre-trained models that can be fine-tuned for downstream tasks.

| Paper | arXiv | Date | Key Innovation |
|-------|-------|------|---------------|
| CLEF: Clinically-Guided ECG Foundation | 2512.02180 | Dec 2025 | Contrastive learning with clinical risk scores, single-lead wearable |
| PhysioWave: Multi-Scale Wavelet-Transformer | 2506.10351 | Jun 2025 | Large-scale pretrained EMG/ECG models with wavelets |
| NormWear: Multivariate Wearable Foundation | 2412.09758 | Dec 2024 | First multi-modal foundation model for ECG + PPG + ACC |
| AnyPPG: ECG-Guided PPG Foundation | 2511.01747 | Nov 2025 | 100K+ hours, ECG-guided PPG representation |

**Best for us**: NormWear (multi-modal: ECG + ACC) or CLEF (single-lead wearable ECG).

## 3. Self-Supervised ECG Representation Learning

Learn useful representations without labels from wearable data.

| Paper | arXiv | Date | Key Innovation |
|-------|-------|------|---------------|
| PLITA: Invariant + Tempo-variant Attributes | 2502.21162 | Feb 2025 | Dual-pathway SSL for single-lead ECG |
| NERULA: Dual-Pathway Self-Supervised | 2405.19348 | May 2024 | Reconstruction + non-contrastive for single-lead ECG |
| Self-supervised physiological transfer | 2011.12121 | Nov 2020 | 280K hours wrist ACC + ECG, cross-modal SSL |

**Best for us**: PLITA — specifically designed for single-lead ECG SSL.

## 4. Neural ODEs for Cardiac Dynamics

| Paper | arXiv | Date | Key Innovation |
|-------|-------|------|---------------|
| Neural State-Space with Causal Disentanglement | 2209.12387 | Sep 2022 | Interacting neural ODEs for cardiac electrical propagation |

This area has fewer papers but is highly relevant for physics-informed modeling of cardiac dynamics.

## 5. Multi-Modal Sensor Fusion

Cross-channel models that combine ECG + ACC + GYRO.

Key approaches from the literature:
- **NormWear** (2412.09758): Multi-modal foundation model for wearable sensing
- **Self-supervised transfer** (2011.12121): Cross-modal (ACC→ECG) physiological learning
- **Adaptive filtering** with IMU reference channels for artifact removal

## Implementation Priority

### Tier 1 — Implement first (most mature, PyTorch-ready)
1. **ECGMamba / BioMamba**: Efficient SSM backbone for ECG classification
2. **CLEF / NormWear**: Foundation model fine-tuning for downstream tasks
3. **Self-supervised pre-training**: PLITA-style dual-pathway SSL

### Tier 2 — Implement next (more specialized)
4. **Neural ODE cardiac dynamics**: Physics-constrained latent dynamics
5. **Differentiable DSP**: Make bandpass/peak-detection learnable end-to-end
6. **Multi-modal fusion**: Joint ECG+ACC+GYRO representation learning

### Tier 3 — Research stage (less mature)
7. **PINNs for ECG**: Constrained by Einthoven/dipole models — very few papers
8. **Physics-constrained artifact removal**: ACC→ECG adaptive filtering with learned components

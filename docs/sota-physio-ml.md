# State-of-the-Art Physics-Based ML for Multimodal Time Series

Research compilation for the movensense physio library. While developed for wearable physiological sensors (ECG + IMU), these architectures are general-purpose: they apply to any domain where multiple synchronized time series encode an underlying physical or dynamical system.

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

## 6. Physics-Informed Architectures (Implemented)

| Model | arXiv | Key Innovation | Module |
|-------|-------|---------------|--------|
| PirateNet | 2402.00326 | Adaptive residual connections, progressive deepening | `learned/pinn.py` |
| Physics-GRU | 2408.16599 | Constrained GRU with smoothness + conservation losses | `learned/pinn.py` |
| Residual-Based Attention | 2509.20349 | Attention weighted by physics residual magnitude | `learned/pinn.py` |
| Symbolic-KAN | 2603.23854 | Kolmogorov-Arnold Networks with B-spline edges, equation discovery | `learned/symbolic.py` |
| WARP | 2506.01153 | Physics-informed linear RNN, 10x better on dynamical systems | Referenced |

## 7. Causal Discovery (Implemented)

| Method | Source | Module |
|--------|--------|--------|
| Granger Causality (VAR F-test) | Classical | `learned/causal.py` |
| Cross-Channel Causality Discovery | Novel | `learned/causal.py` |
| Transfer Entropy (information-theoretic) | Classical | `learned/causal.py` |
| seq2graph (dynamic dependencies) | 1812.04448 | Referenced |
| PCMCI+ (causal time series) | tigramite library | Referenced |

## 8. Symbolic Regression & Equation Discovery

| Paper | arXiv | Date | Key Innovation |
|-------|-------|------|---------------|
| Symbolic-KAN | 2603.23854 | Mar 2026 | KAN + symbolic structure for governing equations |
| LLM-Based Scientific Equation Discovery | 2602.10576 | Feb 2026 | RL-tuned LLMs for symbolic regression |
| Symbolic Foundation Regressor | 2505.21879 | May 2025 | Pre-trained model for networked dynamical systems |
| Symplectic Neural Networks | 2408.09821 | Aug 2024 | Hamiltonian dynamics with symbolic regression |

---

## 9. Why These Models Matter Beyond Wearables

The architectures in this library — PirateNets, PhysicsGRU, KAN, BioSSM, causal discovery, and multi-modal fusion — are not ECG-specific. They are general solutions to a universal problem: **learning from multiple synchronized time series governed by underlying physics**. Below is a summary of their differential advantages and the breadth of domains they unlock.

### Differential Advantages of Each Architecture

| Model | What It Uniquely Does | Why Classical Methods Can't |
|-------|----------------------|---------------------------|
| **PirateNet** | Learns solutions to differential equations while automatically managing network depth. Adaptive residual gates start shallow (stable) and deepen (expressive) during training. | Standard PINNs fail with deep networks due to vanishing gradients in PDE residuals. PirateNet solves this without manual architecture tuning. |
| **PhysicsGRU** | Encodes conservation laws and smoothness priors directly into the recurrent cell. Output can be hard-bounded to physiologically/physically plausible ranges. | Standard RNNs have no mechanism to enforce that energy is conserved, mass balances, or outputs stay within physical limits. They learn these implicitly (if at all) from data. |
| **KAN / PhysicsKAN** | Discovers symbolic governing equations from raw sensor data. Each edge is a learnable B-spline that can be inspected to extract `y = f(x)` relationships. | Neural networks are black boxes. KAN makes the learned function transparent — you can extract `F = ma`-style equations after training. |
| **ResidualAttention** | Focuses model capacity on time regions where the physics model is most wrong. Attention weights are proportional to the magnitude of PDE/ODE residual violation. | Standard attention treats all time steps equally. This architecture automatically concentrates on anomalies, transients, and model failures — exactly where human experts look. |
| **BioSSM** | Linear-time sequence modeling (O(N) vs O(N²) for transformers) with selective state spaces. Bidirectional for offline analysis. Handles 100K+ time steps natively. | Transformers scale quadratically with sequence length. Hour-long recordings at 200Hz = 720K samples — SSMs handle this; transformers cannot. |
| **Causal Discovery** | Identifies directed cause→effect relationships between sensor channels (e.g., "motion causes ECG artifact" or "temperature rise precedes pressure drop"). | Correlation is not causation. Granger causality and transfer entropy test for directed, time-lagged information flow — essential for understanding *why* events happen. |
| **MultiModalFusion** | Cross-modal attention lets each sensor "see" the others. Learns which channels are informative for which events, automatically handling different sampling rates and dimensionalities. | Simple concatenation or late fusion ignores the temporal relationships between modalities. Cross-attention captures "when ACC spikes, ECG distorts" patterns. |

### Downstream Applications by Domain

#### Healthcare & Clinical

| Application | Sensors | Key Models | What's Learned |
|-------------|---------|-----------|---------------|
| **ICU patient monitoring** | ECG, SpO2, ABP, respiration, temperature | MultiModalFusion + PhysicsGRU | Cross-sensor deterioration patterns. PhysicsGRU enforces physiological bounds (HR 30-250, SpO2 0-100%). Causal discovery identifies which vital sign deteriorates first. |
| **Deep brain stimulation (DBS) tuning** | LFP (local field potentials), EMG, accelerometer, patient diaries | BioSSM + CausalDiscovery | Real-time detection of pathological oscillations (beta-band in Parkinson's). Causal model links stimulation parameters → tremor reduction → side effects. KAN discovers stimulation→response transfer functions. |
| **Seizure prediction** | EEG (multi-channel), ECG, ACC | BioSSM + ResidualAttention | SSM handles long EEG sequences. ResidualAttention focuses on pre-ictal anomalies where the brain's normal dynamics break down. |
| **Anesthesia depth monitoring** | EEG, EMG, hemodynamics | PhysicsGRU + MultiModalFusion | Conservation-constrained model of pharmacokinetic drug dynamics. Multi-modal fusion detects awareness events from cross-signal patterns. |
| **Cardiac digital twin** | 12-lead ECG, echocardiography, CT | PirateNet + KAN | PirateNet solves cardiac electrophysiology PDEs (bidomain model). KAN extracts patient-specific conduction parameters. |

#### Neuroscience & Behavioral

| Application | Sensors | Key Models | What's Learned |
|-------------|---------|-----------|---------------|
| **Neurobehavioral phenotyping** | EEG, eye tracking, facial EMG, speech, GSR | MultiModalFusion + CausalDiscovery | Which neural signals drive which behavioral responses. Transfer entropy quantifies information flow from brain→behavior. |
| **Sleep staging** | EEG, EOG, EMG, SpO2, respiratory effort | BioSSM + ResidualAttention | 8-hour recordings at multi-channel. SSM handles the length. ResidualAttention highlights transitions (wake→N1→N2→N3→REM). |
| **Emotion recognition** | ECG, GSR, respiration, voice, facial video | MultiModalFusion + PhysicsGRU | Cross-modal attention learns which physiological signals predict which emotional states. PhysicsGRU models autonomic nervous system dynamics. |
| **Cognitive load assessment** | EEG, pupillometry, typing patterns, HRV | CausalDiscovery + KAN | Causal model: task difficulty → frontal theta EEG → pupil dilation → HRV change. KAN discovers the quantitative relationship. |

#### Industrial & Engineering

| Application | Sensors | Key Models | What's Learned |
|-------------|---------|-----------|---------------|
| **Predictive maintenance** | Vibration (ACC), temperature, current, acoustic emission, oil analysis | PirateNet + CausalDiscovery + KAN | PirateNet learns the machine's nominal dynamics (governing ODEs). Residuals indicate degradation. Causal discovery identifies which sensor predicts failure earliest. KAN extracts remaining-useful-life equations. |
| **Automotive telemetry** | IMU (6/9-axis), wheel speed, steering angle, brake pressure, GPS | MultiModalFusion + PhysicsGRU | PhysicsGRU encodes vehicle dynamics (F=ma, tire slip models). Fusion detects anomalous driving patterns. Conservation loss enforces energy/momentum balance. |
| **Structural health monitoring** | Strain gauges, accelerometers, temperature, humidity | PirateNet + ResidualAttention | PirateNet learns structural dynamics (wave equation). ResidualAttention focuses on cracks/damage where physics model breaks. |
| **Battery degradation** | Voltage, current, temperature, impedance spectroscopy | PhysicsGRU + KAN | PhysicsGRU models electrochemical dynamics with conservation constraints. KAN discovers capacity fade equations. |

#### Environmental & Earth Science

| Application | Sensors | Key Models | What's Learned |
|-------------|---------|-----------|---------------|
| **Seismic event detection** | Seismometer (3-axis), hydrophone, tiltmeter | BioSSM + CausalDiscovery | SSM processes continuous seismic streams. Causal discovery links precursor signals across station network. |
| **Climate sensor networks** | Temperature, humidity, pressure, wind, CO2 | MultiModalFusion + PirateNet | PirateNet encodes atmospheric dynamics (Navier-Stokes simplified). Fusion handles heterogeneous sensor types and sampling rates. |

#### Audio/Video + Sensor Fusion

| Application | Sensors | Key Models | What's Learned |
|-------------|---------|-----------|---------------|
| **Multimodal speech analysis** | Audio waveform, laryngeal EMG, airflow, EGG | MultiModalFusion + CausalDiscovery | Which articulatory gestures cause which acoustic features. Transfer entropy measures speech production→acoustics information flow. |
| **Video + IMU activity recognition** | Camera (pose estimation), ACC, GYRO | MultiModalFusion | Cross-modal attention aligns visual pose with inertial measurements. Handles different frame rates (30fps video vs 200Hz IMU). |
| **Surgical robotics** | Force/torque, video, instrument tracking, tissue imaging | PhysicsGRU + ResidualAttention | PhysicsGRU models tissue mechanics with force-displacement constraints. ResidualAttention identifies moments where tissue behavior deviates from expected (complications). |

### The Common Thread

All of these applications share the same structure:

1. **Multiple synchronized time series** from different sensor modalities
2. **Underlying physical or biological laws** governing the system
3. **Events of interest** that manifest as cross-channel patterns
4. **Need for interpretability** — not just "what happened" but "why"

The physio library's architecture handles this universally:

```
Raw multi-modal streams
    → Per-channel encoding (ChannelEncoder / BioSSM)
    → Cross-modal attention (MultiModalFusion)
    → Physics-constrained dynamics (PirateNet / PhysicsGRU)
    → Causal structure (Granger / Transfer Entropy)
    → Symbolic discovery (KAN → governing equations)
    → Events + interpretable explanations
```

This pipeline is the same whether the input is ECG+ACC from a wearable, vibration+temperature from a turbine, or EEG+EMG from a neuroscience experiment. The physics changes; the architecture doesn't.

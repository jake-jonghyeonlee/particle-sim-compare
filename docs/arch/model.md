# model.py contract (small multi-input autoencoder, torch)

Purpose: learn case feature vectors for comparison/retrieval. Reconstruction is an
internal training objective only; this is not a CA/hotspot predictor.

Batch dict (torch float32 tensors; N = batch size). Keys of disabled modalities may be absent.
- layout (N, CL, H, W), layout_valid (N, CL, H, W), layout_present (N, CL)
- hotspot (N, CH, H, W), hotspot_valid (N, CH, H, W), hotspot_present (N, CH)
- ca (N, 2G), ca_mask (N, 2G): normalized raw CA followed by normalized CA/unit-area
- fab (N, B), fab_mask (N, B), fab_total (N, 1), fab_total_mask (N, 1)
- geom (N, D), geom_mask (N, D)

```python
MODALITIES = ("layout", "hotspot", "ca", "fab", "geometry")
STRUCT_MODALITIES = ("layout", "hotspot", "ca", "geometry")

@dataclass
class ModelConfig:
    image_hw: tuple[int, int] = (64, 64)
    layout_channels: int = 2
    hotspot_channels: int = 3
    ca_dim: int = 5
    fab_dim: int = 5
    geom_dim: int = 4
    enabled: tuple[str, ...] = MODALITIES
    conv_channels: tuple[int, ...] = (8, 16, 32)
    modality_dim: int = 16
    latent_dim: int = 32
    def to_dict(self) -> dict        # tuples -> lists
    @classmethod
    def from_dict(cls, d) -> "ModelConfig"
```
Validation in MultiInputAE.__init__ (ValueError): enabled non-empty subset of
MODALITIES; H and W divisible by 2**len(conv_channels); latent_dim even; a vector
modality with dim 0 or image modality with 0 channels cannot be enabled.

Modules:
- `ImageEncoder(in_ch, conv_channels, modality_dim)`: input cat([x*valid, valid], 1)
  (2*in_ch channels); per conv channel: Conv2d(k=3, stride=2, pad=1) + ReLU;
  AdaptiveAvgPool2d(1); Linear -> modality_dim.
- `ImageDecoder(out_ch, conv_channels, modality_dim, image_hw)`: Linear ->
  (conv_channels[-1], H/2**L, W/2**L); ConvTranspose2d(k=4, stride=2, pad=1) blocks
  mirroring conv_channels (ReLU between), last block outputs out_ch logits (H, W).
- `VectorEncoder(in_dim, modality_dim)`: input cat([x*mask, mask]) ->
  Linear(2*in_dim, 32) -> ReLU -> Linear(32, modality_dim).
- `VectorDecoder(modality_dim_in, out_dim)`: Linear(in, 32) -> ReLU -> Linear(32, out_dim).

`class MultiInputAE(nn.Module)`, `__init__(self, cfg: ModelConfig)`:
- encoders for enabled modalities; fab encoder is a VectorEncoder over
  cat(fab, fab_total) with mask cat(fab_mask, fab_total_mask) (dim fab_dim+1).
- presence (N,) float: layout = layout_present.amax(1); hotspot likewise;
  ca = ca_mask.amax(1); fab = max(fab_mask.amax(1), fab_total_mask.amax(1));
  geometry = geom_mask.amax(1). Each modality embedding is multiplied by presence.
- struct head: Linear(len(enabled struct)*modality_dim -> latent_dim//2) if any
  struct modality enabled; fab head: Linear(modality_dim -> latent_dim//2) if fab enabled.
- z = cat of available [z_struct, z_fab]; decoders take z (dimension = sum of head
  outputs) and reconstruct every enabled modality: recon keys "layout" (N,CL,H,W
  logits), "hotspot" (N,CH,H,W logits), "ca" (N,G), "fab" (N,B), "fab_total" (N,1)
  (one VectorDecoder of size B+1 split), "geometry" (N,D).
- `forward(batch) -> dict`: {"z": Tensor, "z_struct": Tensor | None,
  "z_fab": Tensor | None, "recon": dict[str, Tensor], "present": dict[str, Tensor (N,)]}
- `encode(batch) -> dict` same keys without recon (can reuse forward).

z_struct / z_fab are separately extractable features; this does NOT establish
causal separation of structure and Fab effects.

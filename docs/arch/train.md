# train.py contract (training, checkpoint, embedding)

Imports: random, numpy, torch, model (ModelConfig, MultiInputAE), losses
(masked_losses), split (product_balance_weights), preprocess (Normalizer).

- `select_device(name: str) -> torch.device`: "cpu" -> cpu; "auto" -> cuda if
  available, else mps if available, else cpu; "cuda"/"mps" when unavailable ->
  RuntimeError; other -> ValueError.
- `set_seed(seed: int) -> None`: random, numpy, torch.
- `model_config_from_arrays(arrays: dict, *, enabled, conv_channels, modality_dim,
  latent_dim) -> ModelConfig`: image_hw from layout H,W; channel/dim counts from
  array shapes (layout/hotspot channel 1, ca/fab/geom last dim).
- `to_tensors(arrays: dict, pos, device) -> dict[str, torch.Tensor]`.
- `train_model(arrays, meta, train_pos, model_cfg, *, epochs, batch_size, lr, seed,
  device="cpu", balance_products=True, loss_weights=None)
  -> tuple[MultiInputAE, list[dict]]`
  set_seed(seed); Adam(lr). Each epoch draws len(train_pos) samples: if
  balance_products, torch.multinomial over product_balance_weights(meta, train_pos)
  with replacement and a seeded torch.Generator; else a seeded permutation.
  Mini-batches of batch_size; loss = masked_losses(batch, out, loss_weights).
  History row per epoch: {"epoch": int, "total": mean total, <modality>: mean over
  batches where logged}. Only train_pos rows are ever used.
- `embed(model, arrays, device="cpu", batch_size=64) -> dict[str, np.ndarray | None]`
  eval mode, no_grad; keys "z", "z_struct", "z_fab" (None when that head is absent).
- `save_checkpoint(path, model, model_cfg, normalizers: dict[str, Normalizer],
  extra: dict) -> None`: torch.save({"state_dict", "model_config": cfg.to_dict(),
  "normalizers": {k: n.to_dict()}, "extra": extra}) — plain python types + tensors.
- `load_checkpoint(path, device="cpu") -> tuple[MultiInputAE, ModelConfig,
  dict[str, Normalizer], dict]` using torch.load(weights_only=True); model in eval mode.

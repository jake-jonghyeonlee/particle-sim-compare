"""Contract tests for losses (derived from the orchestrator smoke gate reproducing review findings)."""


def test_contract_gate():
    import sys
    import torch
    from simcompare.losses import LOSS_KINDS, masked_losses
    n = 2
    batch = {"layout": torch.ones(n, 2, 8, 8), "layout_valid": torch.ones(n, 2, 8, 8), "layout_present": torch.tensor([[1., 0.], [0., 0.]]),
             "ca": torch.tensor([[1.0, float("nan")], [2.0, 2.0]]), "ca_mask": torch.tensor([[1., 0.], [0., 0.]]),
             "fab": torch.zeros(n, 2), "fab_mask": torch.zeros(n, 2), "fab_total": torch.zeros(n, 1), "fab_total_mask": torch.zeros(n, 1)}
    recon = {"layout": torch.zeros(n, 2, 8, 8, requires_grad=True), "ca": torch.zeros(n, 2, requires_grad=True),
             "fab": torch.zeros(n, 2), "fab_total": torch.zeros(n, 1)}
    total, logs = masked_losses(batch, {"recon": recon})
    assert torch.isfinite(total) and set(logs) == {"layout", "ca", "total"}
    bce = torch.nn.functional.binary_cross_entropy_with_logits(torch.zeros(()), torch.ones(()))
    assert abs(logs["layout"] - float(bce)) < 1e-5 and abs(logs["ca"] - 1.0) < 1e-6
    assert abs(logs["total"] - (float(bce) + 1.0) / 2) < 1e-5
    total.backward()
    _, logs2 = masked_losses(batch, {"recon": recon}, {"layout": 3.0, "ca": 1.0})
    assert abs(logs2["total"] - (3 * float(bce) + 1.0) / 4) < 1e-5
    empty = {k: torch.zeros_like(v) if k.endswith(("mask", "present")) else v for k, v in batch.items()}
    t0, l0 = masked_losses(empty, {"recon": recon})
    assert float(t0) == 0.0 and t0.requires_grad and set(l0) == {"total"}
    assert LOSS_KINDS["layout"] == "bce_binary"

"""CPU-only compatibility seam; no CUDA discovery or installation in prototype."""
def prepare_linux_cuda():
    return {"state": "not_applicable", "message": None}

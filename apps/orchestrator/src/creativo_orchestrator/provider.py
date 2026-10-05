class ProviderNotConfigured(RuntimeError):
    pass


class GpuProvider:
    """A future autoscaler. Dispatch never calls this.

    A model joins by running its worker container beside Redis. The worker
    registers itself. The machine underneath can change without changing the
    images, the queue, or the studio.
    """

    async def provision(self, model_id: str, gpu: str) -> str:
        raise ProviderNotConfigured(model_id)


class RunPodProvider(GpuProvider):
    """A placeholder for renting a machine later. The running stack does not call it."""

    def __init__(self, api_key: str = "", template_id: str = "") -> None:
        self.api_key = api_key
        self.template_id = template_id

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.template_id)

    async def provision(self, model_id: str, gpu: str) -> str:
        raise ProviderNotConfigured(model_id)

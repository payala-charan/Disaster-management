"""Service-layer errors. The API maps them to HTTP responses (see app/main.py)."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    """The request is valid but the current state forbids it (invalid transition, resource taken, ...)."""

    status_code = 409


class Invalid(ServiceError):
    status_code = 422


def get_or_404(db, model, id_: str, *, for_update: bool = False):
    obj = db.get(model, id_, with_for_update=for_update)
    if obj is None:
        raise NotFound(f"{model.__name__} {id_} not found")
    return obj

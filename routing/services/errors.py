class RoutingError(Exception):
    """Base class; `status` is the HTTP status the API should return."""
    status = 400


class InvalidLocation(RoutingError):
    status = 400


class LocationNotInUSA(InvalidLocation):
    pass


class RouteNotFound(RoutingError):
    status = 422


class UpstreamError(RoutingError):
    status = 502

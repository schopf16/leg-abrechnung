"""Every page route is registered, and registered to a real page function.

Written after `/backup` answered HTTP 422 for a whole session: a helper
had been inserted between `@ui.page("/backup")` and the function it
decorated, so the *helper* became the route. FastAPI then wanted its
`info` argument as a query parameter, every request was rejected, and the
actual page was not registered at all.

Nothing caught it. The suite's other GUI tests call the page functions
directly, which bypasses routing entirely, so they kept passing while the
route was broken. These checks look at the routing table instead.
"""

import inspect

import pytest

from app.gui import pages as _pages  # noqa: F401  -- registers every route

#: Routes the app must expose, each the entry point of one page. A new
#: page belongs here; a page that disappears from the routing table is a
#: bug, not a test to delete.
EXPECTED_ROUTES = [
    "/",
    "/assignments",
    "/backup",
    "/billing",
    "/dunning",
    "/email-dispatch",
    "/import",
    "/legs",
    "/metering-points",
    "/persons",
    "/receivables",
    "/reports",
    "/settings",
    "/signatures",
    "/sites",
    "/statistics",
    "/substation-areas",
    "/web-registrations",
]


def _page_routes() -> dict:
    """Collect the app's page routes and their endpoint functions.

    Returns:
        `{path: endpoint}` for every registered route that has one.
    """
    from nicegui import app as nicegui_app

    return {
        route.path: route.endpoint
        for route in nicegui_app.routes
        if getattr(route, "path", "").startswith("/") and getattr(route, "endpoint", None) is not None
    }


@pytest.mark.parametrize("path", EXPECTED_ROUTES)
def test_the_route_exists(path):
    """A page that vanished from the routing table cannot be opened at all."""
    assert path in _page_routes(), f"Route {path} ist nicht registriert"


@pytest.mark.parametrize("path", EXPECTED_ROUTES)
def test_the_route_points_at_a_page_function_taking_no_arguments(path):
    """The endpoint must be a page, not a helper that happened to sit below it.

    A page function takes nothing: everything it needs it reads itself.
    An endpoint with a required parameter is FastAPI's cue to demand a
    query argument, which turns every visit into HTTP 422 -- exactly what
    happened to `/backup`.
    """
    # NiceGUI registers a wrapper that takes the request; the function
    # actually decorated hangs off it as `__wrapped__`, and that is the
    # one whose signature decides what FastAPI will demand.
    endpoint = _page_routes()[path]
    page_function = getattr(endpoint, "__wrapped__", endpoint)
    required = [
        name
        for name, parameter in inspect.signature(page_function).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]

    assert not required, (
        f"Route {path} zeigt auf {page_function.__name__}, das {required} verlangt -- "
        "vermutlich steht der @ui.page-Dekorator über der falschen Funktion"
    )


@pytest.mark.parametrize("path", EXPECTED_ROUTES)
def test_the_endpoint_is_not_a_private_helper(path):
    """A name starting with an underscore is never meant to be a page."""
    endpoint = _page_routes()[path]
    page_function = getattr(endpoint, "__wrapped__", endpoint)

    assert not page_function.__name__.startswith("_"), (
        f"Route {path} zeigt auf die private Funktion {page_function.__name__}"
    )

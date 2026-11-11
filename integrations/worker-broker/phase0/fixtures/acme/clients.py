"""API clients: v1 is deprecated, v2 is current."""
import warnings


class ApiClientV1:
    """Deprecated since 2026-03. Use ApiClientV2."""

    def __init__(self, base_url):
        warnings.warn("ApiClientV1 is deprecated, use ApiClientV2", DeprecationWarning, stacklevel=2)
        self.base_url = base_url

    def get_item(self, widget_id):
        return {"widget_id": widget_id, "endpoint": self.base_url + "/v1/item"}

    def list_items(self, page_size=50):
        return {"page_size": page_size, "cursor": None, "endpoint": self.base_url + "/v1/items"}


class ApiClientV2:
    """Current client. Changes vs v1:

    - get_item -> fetch(item_ref), item_ref is a part_id string ("p-1"), not widget_id
    - list_items -> iterate_items(page_size=100), returns a generator (no cursors)
    - constructor takes (base_url, api_key) — api_key is required
    """

    def __init__(self, base_url, api_key):
        if not api_key:
            raise ValueError("api_key is required in v2")
        self.base_url = base_url
        self.api_key = api_key

    def fetch(self, item_ref):
        return {"item_ref": item_ref, "endpoint": self.base_url + "/v2/item"}

    def iterate_items(self, page_size=100):
        yield {"page_size": page_size, "endpoint": self.base_url + "/v2/items"}

"""Dynamic adapter registry (adapters.get_adapter)."""

from painminer.adapters import get_adapter
from painminer.adapters.github import GithubAdapter
from painminer.adapters.hackernews import HackerNewsAdapter
from painminer.adapters.json_api import JsonApiAdapter

client = object()  # adapters store the client but never call it at construction


def test_resolves_github_impl():
    source = {
        "name": "gh",
        "adapter": "json_api",
        "config_json": {"adapter_impl": "github", "base_url": "https://api.github.com"},
    }
    assert isinstance(get_adapter(source, client), GithubAdapter)


def test_resolves_hackernews_impl():
    source = {
        "name": "hn",
        "adapter": "json_api",
        "config_json": {
            "adapter_impl": "hackernews",
            "base_url": "https://hn.algolia.com/api/v1",
        },
    }
    assert isinstance(get_adapter(source, client), HackerNewsAdapter)


def test_resolves_json_api_impl():
    source = {
        "name": "generic",
        "adapter": "json_api",
        "config_json": {"adapter_impl": "json_api", "base_url": "https://example.com/api"},
    }
    assert isinstance(get_adapter(source, client), JsonApiAdapter)


def test_falls_back_to_adapter_column_when_no_impl_configured():
    source = {
        "name": "generic",
        "adapter": "json_api",
        "config_json": {"base_url": "https://example.com/api"},
    }
    assert isinstance(get_adapter(source, client), JsonApiAdapter)


def test_rejects_unknown_impl():
    source = {
        "name": "ghost",
        "adapter": "json_api",
        "config_json": {"adapter_impl": "nope_not_real", "base_url": "https://example.com"},
    }
    try:
        get_adapter(source, client)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown adapter_impl")


def test_rejects_unsafe_impl_name_path_traversal():
    source = {
        "name": "evil",
        "adapter": "json_api",
        "config_json": {"adapter_impl": "../evil", "base_url": "https://example.com"},
    }
    try:
        get_adapter(source, client)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unsafe adapter_impl")


def test_rejects_unsafe_impl_name_bad_chars():
    source = {
        "name": "bad",
        "adapter": "json_api",
        "config_json": {"adapter_impl": "Bad-Name", "base_url": "https://example.com"},
    }
    try:
        get_adapter(source, client)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unsafe adapter_impl")

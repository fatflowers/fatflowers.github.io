import pytest

from intelligence.mcp.errors import MCPContractError
from intelligence.mcp.registry import MCPToolRegistry


REGISTRY = {
    "version": 1,
    "server": {"name": "aisa-tools", "url": "https://tools.aisa.one/mcp"},
    "tools": {
        "twitter-v1": {
            "status": "verified",
            "channel_types": ["twitter"],
            "tool_name": "get_twitter_user_tweet_timeline",
            "input_template": {
                "userId": "{{ channel.resolved_user_id }}",
                "cursor": "{{ cursor.next }}",
                "includeReplies": False,
            },
            "output_adapter": "twitter_posts_v1",
            "pagination": {"type": "cursor", "field": "cursor"},
        }
    },
}


def test_registry_renders_types_and_omits_missing_cursor():
    binding = MCPToolRegistry.from_mapping(REGISTRY).get("twitter-v1")
    binding.assert_runnable("twitter")
    assert binding.render_arguments({"resolved_user_id": "123"}) == {
        "userId": "123",
        "includeReplies": False,
    }


def test_scheduled_binding_must_be_verified():
    data = {**REGISTRY, "tools": {"twitter-v1": {**REGISTRY["tools"]["twitter-v1"], "status": "schema_verified"}}}
    binding = MCPToolRegistry.from_mapping(data).get("twitter-v1")
    with pytest.raises(MCPContractError):
        binding.assert_runnable("twitter", scheduled=True)
    binding.assert_runnable("twitter", scheduled=False)


def test_binding_rejects_wrong_channel_type():
    binding = MCPToolRegistry.from_mapping(REGISTRY).get("twitter-v1")
    with pytest.raises(MCPContractError):
        binding.assert_runnable("reddit")


def test_registry_supports_reviewed_nested_config_and_default_filter():
    data = {
        **REGISTRY,
        "tools": {
            "twitter-v1": {
                **REGISTRY["tools"]["twitter-v1"],
                "input_template": {
                    "userId": "{{ channel.config.resolved_user_id }}",
                    "includeReplies": "{{ channel.config.include_replies | default(false) }}",
                    "limit": "{{ channel.config.limit | default(20) }}",
                },
            }
        },
    }
    binding = MCPToolRegistry.from_mapping(data).get("twitter-v1")
    rendered = binding.render_arguments({"config": {"resolved_user_id": "123"}})
    assert rendered == {"userId": "123", "includeReplies": False, "limit": 20}

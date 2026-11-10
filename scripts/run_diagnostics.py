"""Count event categories without retaining commands, paths, or tool results."""

ITEM_TYPES = ("command_execution", "mcp_tool_call", "web_search", "file_change", "agent_message", "reasoning", "plan", "other")
TOOL_TYPES = {"command_execution", "mcp_tool_call", "web_search"}


class EventDiagnostics:
    def __init__(self):
        self.counts = dict.fromkeys(ITEM_TYPES, 0)
        self.seen = set()
        self.items_without_id = 0

    def consume(self, item_id, item_type):
        if isinstance(item_id, str) and item_id:
            if item_id in self.seen:
                return
            self.seen.add(item_id)
        else:
            self.items_without_id += 1
        category = item_type if isinstance(item_type, str) and item_type in self.counts else "other"
        self.counts[category] += 1

    def result(self, complete=False, started=True):
        return {
            "status": "not_started" if not started else "observed" if complete and not self.items_without_id else "partial",
            "completed_items": dict(self.counts),
            "completed_tool_items": sum(self.counts[name] for name in TOOL_TYPES),
            "items_without_id": self.items_without_id,
            "model_calls": None,
        }

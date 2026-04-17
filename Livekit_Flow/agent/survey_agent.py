from livekit.agents import Agent


class HelloBot(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly voice assistant. "
                "Greet the user warmly, say hi and hello, and have a simple natural conversation. "
                "Keep responses short and conversational."
            ),
        )

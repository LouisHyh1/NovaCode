"""Team 实验特性读取。"""


def fork_teammate_enabled(config) -> bool:
    return bool(config.features.fork_teammate)

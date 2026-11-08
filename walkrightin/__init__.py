def run(config, out, **kwargs):
    """Run the sources, check their links, and walk the platforms' items they reach. Return a Result."""
    from walkrightin.walk import run as execute  # noqa: PLC0415

    return execute(config, out, **kwargs)

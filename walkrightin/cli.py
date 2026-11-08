import dataclasses
import logging
import sys

import click

from walkrightin import config as configuration
from walkrightin.model import Link


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Log each step")
def main(verbose):
    """Find the files that public sources link to on platforms like Google Drive, and check whether each opens."""
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s", level=logging.INFO if verbose else logging.WARNING
    )


@main.command()
@click.option("-c", "--config", "path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("-o", "--out", type=click.Path(file_okay=False), required=True, help="The run's directory")
@click.option("--source", "only", multiple=True, help="Run only this source again, in an existing run")
@click.option("--depth", type=int, help="Override [walk] max_depth")
@click.option("--no-walk", is_flag=True, help="Only run the sources and check their links")
@click.option("--resume", is_flag=True, help="Continue an interrupted walk")
def run(path, out, only, depth, no_walk, resume):
    """Run the sources, check their links, and walk the platforms' items they reach."""
    from walkrightin.walk import run as execute  # noqa: PLC0415

    config = configuration.load(path)
    if depth is not None:
        config.walk.max_depth = depth
    result = execute(config, out, only=only, resume=resume, walk=not no_walk)
    click.echo(f"{result.links} links, {result.items} items")
    if result.skipped_for_size:
        click.echo(f"skipped for size: {result.skipped_for_size}")
    if result.rate_limited:
        sys.exit(f"{result.rate_limited} checks were rate-limited. Run again later, with --resume if interrupted.")


@main.command()
@click.argument("urls", nargs=-1, required=True)
def check(urls):
    """Check how an anonymous visitor sees each URL."""
    from walkrightin.check import Checker  # noqa: PLC0415

    checker = Checker(delay=0)
    for url, result in checker.check_all(urls).items():
        click.echo(f"{result['status']}\t{result['http']}\t{url}")


@main.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--bare-ids", is_flag=True, help="Also find platforms' IDs outside URLs")
def extract(paths, bare_ids):
    """Print the links to platforms in each file, as tab-separated values."""
    from walkrightin.extract import Extractor  # noqa: PLC0415
    from walkrightin.shortlinks import ShortLinks  # noqa: PLC0415

    extractor = Extractor(ShortLinks(), bare_ids=bare_ids)
    fields = [f.name for f in dataclasses.fields(Link)][1:]
    click.echo("\t".join(["path", *fields]))
    for path in paths:
        links, error = extractor.file(path)
        if error:
            click.echo(f"{path}: {error}", err=True)
        for link in links:
            click.echo("\t".join([path, *(str(getattr(link, f)) for f in fields)]))


@main.command()
def sources():
    """List the installed source types."""
    from walkrightin.sources import registry  # noqa: PLC0415

    for name, cls in sorted(registry().items()):
        click.echo(f"{name}\t{(cls.__doc__ or '').strip().splitlines()[0]}")


@main.command()
def platforms():
    """List the installed platforms."""
    from walkrightin.platforms import registry  # noqa: PLC0415

    for name, cls in sorted(registry().items()):
        click.echo(f"{name}\t{(cls.__doc__ or '').strip().splitlines()[0]}")


if __name__ == "__main__":
    main()

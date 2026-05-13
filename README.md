# runner-sizechart

Small GitHub Actions probe for checking hosted runner CPU, RAM, and disk space.

## Usage

Run **Runner Size Chart** from the Actions tab with `workflow_dispatch`.

The workflow probes:

- `ubuntu-latest`
- `ubuntu-24.04-arm`
- `windows-latest`
- `windows-2022`
- `macos-latest`
- `macos-15-intel`

It also runs an Ubuntu comparison job with `jlumbroso/free-disk-space@main` so the report can show before/after disk availability.

## Output

The final job writes a Markdown report to the GitHub Actions job summary and uploads a `runner-sizechart-report` artifact containing:

- `runner-sizechart.md`
- `runner-sizechart.json`

Per-runner raw snapshots are also uploaded as `runner-sizechart-*` artifacts.

## Notes

The probe is intentionally lightweight. Cleanup candidate sizes are best-effort, and Windows scans are time-limited so they may be partial.

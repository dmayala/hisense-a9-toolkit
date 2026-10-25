# Matching platform policy inputs

These gzip files are the public Android/LineageOS23.2 GSI CIL inputs (vendor
policy interface202504) used for the integrated vendor's existing policy link.
They replace the runtime dependency on the archived
`plain-deadline-20261022/policy-platform` cache. `inputs.json` pins the expanded
bytes. Gzip timestamps are zero; all typepermissive declarations are removed
as in the enforcing77b60ba compiler path. Empty product/mapping inputs are
intentional. Android SELinux policy sources are Apache-2.0 licensed.

The compiler and version flags remain the existing otatools secilc command.
Each invocation expands these files into its own work directory; no build
archive or shared cache is created or modified. No firmware or device-specific
private data is included.

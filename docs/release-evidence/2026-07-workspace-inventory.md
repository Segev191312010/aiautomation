# Phase A Workspace Binary Inventory

Date: 2026-07-09
Phase: A - Truth, Safety, and Product Consolidation
Stage: A1 - Workspace binary inventory

## Scan Scope

The Phase A inventory scanned the repository root for local binary drops with
extensions that do not belong beside source code: `.dll`, `.exe`, `.msi`,
`.zip`, `.rar`, `.7z`, `.dmg`, and `.pkg`.

Tracked source inventory check:

```text
git ls-files | rg -i '\.(dll|exe|msi|zip|rar|7z|dmg|pkg)$'
```

Result: no tracked files with these binary extensions.

## Binary-Like Root Artifacts

| Path | Bytes | SHA-256 | Git state | Signature | Bucket | Project-required | Disposition |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `Zed-x86_64.exe` | 80,590,112 | `F93014DC720AD772D43DB8E9B2B705CCDB2F3ADB665F4BFBF184F8D8D872A16D` | ignored | Valid - Zed Industries Inc | local developer artifact | no | quarantine outside repo |
| `whql-amd-software-adrenalin-edition-26.2.2-win11-b.exe` | 952,645,264 | `452732FAAAF1A0D985A14C8E750A9F356E6F52E224631CE5C4C376DE65D4A221` | ignored | Valid - Advanced Micro Devices | local developer artifact | no | quarantine outside repo |
| `amd-software-adrenalin-edition-26.3.1-minimalsetup-260317_web.exe` | 48,573,312 | `C5BBB2AF7BDEC9501E7E6E99AA4B7AE7EC98252C1629CE1E5DEB205DFC01E310` | ignored | Valid - Advanced Micro Devices | local developer artifact | no | quarantine outside repo |
| `amd-software-adrenalin-edition-26.2.2-minimalsetup-260225_web.exe` | 48,587,080 | `246148C1232BD8D0B5E6B353F6B5F07C1A5D639C22D5CEC6AB8B1C2482F044CF` | ignored | Valid - Advanced Micro Devices | local developer artifact | no | quarantine outside repo |
| `LM-Studio-0.4.12-1-x64.exe` | 607,417,488 | `8B8240682E23815CE673B1680FD81DF00AA97F0F3FF395BA31C0AD55C6675CA3` | ignored | Valid - Element Labs Inc. | local developer artifact | no | quarantine outside repo |
| `Dismays_Chameleon_Tool_2.2.1_[unknowncheats.me]_.dll` | 2,333,184 | `EDA1182C770737575437CC5C5C1109702AA7554DBD967AB088E4B9E355A88EDE` | untracked | NotSigned | unknown/untrusted | no | quarantine outside repo |
| `MelonLoader.Installer.exe` | 21,574,730 | `A32F508050DBDA03F7DE9F1F3DD1AE400135E9DDF03956EDB9AF3464CEED3F8F` | ignored | NotSigned | local developer artifact, unsigned | no | quarantine outside repo |
| `amd-software-adrenalin-edition-26.6.1-minimalsetup-260601_web.exe` | 47,818,496 | `B727DAD4C197B343CBFD93E50AE7F03862710D2781E977F56C58AD21C84971E5` | ignored | Valid - Advanced Micro Devices | local developer artifact | no | quarantine outside repo |

## Bucket Summary

| Bucket | Files |
| --- | --- |
| required test fixture | none |
| required build input | none |
| local developer artifact | 7 |
| unknown/untrusted | 1 |

## Completed Disposition

All root-level binary artifacts listed above were moved out of the active source
tree to:

```text
C:\Users\segev\sdvesdaW\trading-artifact-quarantine\2026-07-phase-a
```

No binary artifact was deleted as part of this cleanup.

Verification:

```text
python scripts/check_workspace_hygiene.py
Workspace hygiene OK: no forbidden binary artifacts found.
```

Probe test:

```text
phase-a-hygiene-probe.dll -> hygiene check failed as expected
probe removed -> hygiene check passed
```

## Ongoing Policy

- Keep root-level binary artifacts outside the active repository tree.
- Keep hashes in this evidence file so any later investigation has an immutable
  reference.
- Run `python scripts/check_workspace_hygiene.py` before Phase A release,
  runtime, and documentation commits.
- Keep release/build outputs outside the source tree unless an explicit release
  process says otherwise.

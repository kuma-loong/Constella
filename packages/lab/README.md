# Constella Lab

Optional lab edition for Constella. It adds Cloudflare Access identity, local roles,
audit records, and multi-node Linux account binding while keeping the public core
package independent.

```bash
pip install constella-gpu-lab
constella-lab --help
```

See the [deployment guide](https://github.com/kuma-loong/Constella/blob/main/docs/LAB_DEPLOYMENT.md)
and [user-system design](https://github.com/kuma-loong/Constella/blob/main/docs/user-system-design-zh.md)
for the required fail-closed configuration, Cloudflare Access setup, node rollout,
security model, and backup procedure.

#!/usr/bin/env python3
"""Post-process `deck file kong2tf` output for the gateway Terraform stack.

kong2tf emits a standalone file with its own `variable "control_plane_id"` (placeholder
default). In this repo the control plane is created by the PLATFORM stack, so we:
  1. drop that variable block,
  2. point every resource at local.control_plane_id (read from the platform stack's state),
  3. escape string contents for HCL: kong2tf writes values raw, so a regex like
     "\\b(essay)\\b" in ai-prompt-guard becomes an invalid HCL escape. Backslashes are
     doubled and template sequences (${ / %{) are escaped so Kong receives the exact value,
  4. stamp a "generated - do not edit" header with the environment.

Usage: postprocess_kong2tf.py <kong2tf-output.tf> <destination.tf> <env>
"""
import re
import sys

src, dst, env = sys.argv[1], sys.argv[2], sys.argv[3]
tf = open(src, encoding="utf-8").read()

tf, n_var = re.subn(r'variable\s+"control_plane_id"\s*\{[^}]*\}\s*', "", tf, count=1)
tf, n_ref = re.subn(r"\bvar\.control_plane_id\b", "local.control_plane_id", tf)
if n_var != 1 or n_ref == 0:
    sys.exit(f"unexpected kong2tf output (variable blocks removed={n_var}, references={n_ref})")
# 3. HCL escaping (kong2tf emits values raw; HCL code itself never contains backslashes)
tf = re.sub(r'\\(?!")', r'\\\\', tf)
tf = tf.replace("${", "$${").replace("%{", "%%{")

if "sk-" in tf and re.search(r'"Bearer sk-[A-Za-z0-9_-]{10,}', tf):
    sys.exit("refusing to write: plaintext provider key found in generated Terraform")

header = (
    f"# GENERATED for environment '{env}' by scripts/build-gateway.sh - DO NOT EDIT.\n"
    "# Source of truth: apiops/openapi/*.yaml (API teams) + apiops/policies|platform|patches (platform team).\n"
    "# Pipeline: openapi2kong -> add-plugins -> merge -> patch -> lint/validate -> kong2tf -> this file.\n\n"
)
open(dst, "w", encoding="utf-8").write(header + tf)
print(f"   kong2tf: {n_ref} resources wired to local.control_plane_id")

"""E5: the port-variable rule applied to the fastapi template's resolved model and to synthetic cases."""

import json
import re
import sys

NOT_NAME_CHAR = re.compile(r"[^A-Z0-9]")


def variable_name(service: str, target: int, protocol: str) -> str:
    return f"WTENV_PORT_{NOT_NAME_CHAR.sub('_', service.upper())}_{target}_{protocol.upper()}"


def names(mappings: list[tuple[str, int, str]]) -> None:
    seen: dict[str, str] = {}
    for service, target, protocol in mappings:
        name = variable_name(service, target, protocol)
        source = f"{service}:{target}/{protocol}"
        if name in seen:
            print(f"  {source:<22} -> {name}   COLLISION with {seen[name]} -> up refuses (FR-012)")
        else:
            print(f"  {source:<22} -> {name}")
            seen[name] = source


with open(sys.argv[1]) as file:
    document = json.load(file)
template = [
    (service, port["target"], port.get("protocol", "tcp"))
    for service, settings in sorted(document["services"].items())
    for port in settings.get("ports") or []
]
print("fastapi template (compose.yml + compose.override.yml, every profile, Compose 2.24.4):")
names(template)
print("synthetic:")
names(
    [("coredns", 53, "udp"), ("coredns", 53, "tcp"), ("api.v2", 8000, "tcp"), ("2fa", 9000, "tcp")]
)
print("collisions:")
names([("my-api", 8000, "tcp"), ("my_api", 8000, "tcp")])
names([("API", 80, "tcp"), ("api", 80, "tcp")])
names([("web", 6379, "tcp"), ("web", 6379, "tcp")])  # one container port published twice (E1.6b)
names([("a-b", 1, "tcp"), ("a--b", 1, "tcp")])

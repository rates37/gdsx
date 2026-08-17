"""The netlist types"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Instance:
    name: str
    cell: str
    connections: dict[str, str] = field(default_factory=dict)  # pin -> net name


@dataclass
class Netlist:
    top: str
    instances: list[Instance] = field(default_factory=list)
    nets: dict[str, list[str]] = field(default_factory=dict)  # net -> ["inst/pin", ...]
    ports: dict[str, str] = field(default_factory=dict)  # net name -> direction
    power_nets: set[str] = field(default_factory=set)
    floating: list[str] = field(default_factory=list)  # "inst/pin" with no net
    conflicts: list[str] = field(
        default_factory=list
    )  # "inst/pin" connecting to multiple nets

    @classmethod
    def from_dict(cls, data: dict) -> "Netlist":
        """Rebuild a netlist written by `to_dict`

        Slices, sub-blocks and cached extractions all come back this way, which
        lets one command's output be another command's input.
        """
        nl = cls(
            top=data["top"],
            ports=dict(data.get("ports", {})),
            power_nets=set(data.get("power_nets", ())),
            floating=list(data.get("floating", ())),
            conflicts=list(data.get("conflicts", ())),
        )
        nl.instances = [
            Instance(i["name"], i["cell"], dict(i["connections"]))
            for i in data["instances"]
        ]
        nl.nets = {net: list(refs) for net, refs in data.get("nets", {}).items()}
        if not nl.nets:  # rebuild the index if it was not stored
            for inst in nl.instances:
                for pin, net in inst.connections.items():
                    nl.nets.setdefault(net, []).append(f"{inst.name}/{pin}")
            for net in nl.nets:
                nl.nets[net].sort()
        return nl

    def to_dict(self) -> dict:
        return {
            "top": self.top,
            "ports": self.ports,
            "power_nets": sorted(self.power_nets),
            "instances": [
                {"name": i.name, "cell": i.cell, "connections": i.connections}
                for i in self.instances
            ],
            "nets": self.nets,
            "floating": self.floating,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True)
class Ref:
    """One pin on one instance, and which way it faces"""

    instance: str
    pin: str
    cell: str
    direction: str  # input | output | power

    def __str__(self) -> str:
        arrow = "<-" if self.direction == "output" else "->"
        return f"{arrow} {self.instance}.{self.pin} ({self.cell})"

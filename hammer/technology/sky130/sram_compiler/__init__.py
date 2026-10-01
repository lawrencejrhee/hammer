import os

from hammer.vlsi import (
    MMMCCorner,
    MMMCCornerType,
    HammerTool,
    HammerToolStep,
    HammerSRAMGeneratorTool,
    SRAMParameters,
)
from hammer.tech import Corner, Supplies, Provide
from hammer.vlsi.units import VoltageValue, TemperatureValue
from hammer.tech import Library, ExtraLibrary
from typing import NamedTuple, Dict, Any, List, Optional
from abc import ABCMeta, abstractmethod


class SKY130SRAMGenerator(HammerSRAMGeneratorTool):
    def tool_config_prefix(self) -> str:
        return "sram_generator.sky130"

    def version_number(self, version: str) -> int:
        return 0

    # Run generator for a single sram and corner
    def generate_sram(self, params: SRAMParameters, corner: MMMCCorner) -> ExtraLibrary:
        cache_dir = os.path.abspath(self.technology.cache_dir)
        speed_name: Optional[str] = None
        # TODO: this is really an abuse of the corner stuff
        if corner.type == MMMCCornerType.Setup:
            speed_name = "slow"
            speed = "SS"
            # self.logger.error("SKY130 SRAM cache does not support corner: {}".format(speed_name))
        elif corner.type == MMMCCornerType.Hold:
            speed_name = "fast"
            speed = "FF"
            # self.logger.error("SKY130 SRAM cache does not support corner: {}".format(speed_name))
        elif corner.type == MMMCCornerType.Extra:
            speed_name = "typical"
            speed = "TT"

        if params.family != "1rw" and params.family != "1rw1r":
            self.logger.error(
                "SKY130 SRAM cache does not support family:{f}".format(f=params.family)
            )
            return ExtraLibrary(prefix=None, library=None)  # type: ignore

        # SRAM22 SRAMs
        if params.name.startswith("sram22"):
            self.logger.info(f"Compiling {params.family} memories to SRAM22 instances")
            # s=round(round(params.width*params.depth/8, -3)/1000) # size in kiB
            w = params.width
            d = params.depth
            sram_name = params.name
            # TODO: replace this if SRAM22 characterization done for other corners
            # we only have typical lib for sky130 srams
            temp = corner.temp.value_in_units("C")
            corner_str = "{speed}_{temp}C_{volt}".format(
                speed=speed.lower(),
                volt="{:.2f}".format(corner.voltage.value_in_units("V")).replace(
                    ".", "v"
                ),
                temp=(
                    "{:03g}".format(temp).replace(".", "p")
                    if temp > 0
                    else "n{:02g}".format(-temp).replace(".", "p")
                ),
            )

            base_dir = self.get_setting("technology.sky130.sram22_sky130_macros")
            found = False
            lib_path: Optional[str] = None
            for fidelity in [".rcc", ".rc", ".c", ""]:
                lib_path = "{b}/{n}/{n}_{c}{f}.lib".format(b=base_dir, n=sram_name, c=corner_str, f=fidelity)
                if os.path.exists(lib_path):
                    found = True
                    break
                else:
                    self.logger.warning(f"SKY130 {params.name} SRAM cache does not support corner {corner_str} with {fidelity} extraction")
            if not found:
                self.logger.error(f"SKY130 {params.name} SRAM cache does not support corner {corner_str}")

            lef_file = "{b}/{n}/{n}.lef".format(b=base_dir, n=sram_name)
            if not os.path.exists(lef_file):
                self.logger.error(f"No LEF for: {sram_name} ({lef_file})")

            return ExtraLibrary(
                prefix=None,
                library=Library(
                    name=sram_name,
                    nldm_liberty_file=lib_path,
                    lef_file="{b}/{n}/{n}.lef".format(b=base_dir, n=sram_name),
                    gds_file="{b}/{n}/{n}.gds".format(b=base_dir, n=sram_name),
                    verilog_sim="{b}/{n}/{n}.v".format(b=base_dir, n=sram_name),
                    corner=Corner(
                        nmos=speed_name,
                        pmos=speed_name,
                        temperature=str(corner.temp.value_in_units("C")) + " C",
                    ),
                    supplies=Supplies(
                        VDD=str(corner.voltage.value_in_units("V")) + " V", GND="0 V"
                    ),
                    provides=[Provide(lib_type="sram", vt=params.vt)],
                ),
            )

        else:
            self.logger.error(f"SRAM {params.name} not supported")
            return ExtraLibrary(prefix=None, library=Library())


tool = SKY130SRAMGenerator
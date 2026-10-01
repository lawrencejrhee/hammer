#  SKY130 plugin for Hammer.
#
#  See LICENSE for licence details.

import functools
import importlib
import importlib.resources
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import List
from typing import Callable, Iterable, List, Optional

from hammer.tech import (
    Corner,
    Decimal,
    DRCDeck,
    HammerTechnology,
    Library,
    LVSDeck,
    Metal,
    PathPrefix,
    Provide,
    Site,
    Stackup,
    Supplies,
    TechConfig,
)
from hammer.tech.specialcells import CellType, SpecialCell
from hammer.utils import (LEFUtils, add_lists, reduce_list_str)
from hammer.vlsi import (
    HammerDRCTool,
    HammerLVSTool,
    HammerPlaceAndRouteTool,
    HammerTool,
    HammerToolHookAction,
    HierarchicalMode,
    TCLTool,
)

from hammer.tech.specialcells import CellType, SpecialCell

from hammer.utils import LEFUtils
from hammer.vlsi import (
    HammerDRCTool,
    HammerLVSTool,
    HammerPlaceAndRouteTool,
    HammerTool,
    HammerToolHookAction,
    HierarchicalMode,
    TCLTool,
)

class SKY130Tech(HammerTechnology):
    """
    Override the HammerTechnology used in `hammer_tech.py`
    This class is loaded by function `load_from_json`, and will pass the `try` in `importlib`.
    """
    
    def gen_config(self) -> None:
        """Generate the tech config, based on the library type selected"""
        
        slib = self.get_setting("technology.sky130.stdcell_library")
        SKY130A = self.get_setting("technology.sky130.sky130A")
        SKY130_CDS = self.get_setting("technology.sky130.sky130_cds")
        SKY130_SCL = self.get_setting("technology.sky130.sky130_scl")
        SRAM22 = self.get_setting("technology.sky130.sram22_sky130_macros")
        
        # SKY130_IO = self.get_setting("technology.sky130.io_periphery_cells") # TODO(jimfang) make it so that users dont need to install entire openPDKs just for the IO cells... thisi may mean republishing the IO cells (after the installer's ef_io io cells modification) at a github location... for now, we depend on SKY130A (open pdks) even for the CDNS std cell flow...
        
        # Common tech LEF and IO cell spice netlists
        libs = []
        if slib == "sky130_fd_sc_hd":
            libs += [
                Library(
                    lef_file=os.path.join(
                        SKY130A,
                        "libs.ref/sky130_fd_sc_hd/techlef/sky130_fd_sc_hd__nom.tlef",
                    ),
                    verilog_sim=os.path.join(
                        SKY130A, "libs.ref/sky130_fd_sc_hd/verilog/primitives.v"
                    ),
                    provides=[Provide(lib_type="technology")],
                ),
            ]
        elif slib == "sky130_scl":
            libs += [
                Library(
                    lef_file="cache/sky130_scl_9T.tlef",
                    verilog_sim=os.path.join(SKY130_SCL, "sky130_scl_9T/verilog/sky130_scl_9T.v"),
                    provides=[Provide(lib_type="technology")],
                ),
            ]
            libs += [
                Library(
                    lef_file=os.path.join(SKY130_SCL, "sky130_scl_9T_tech/lef/sky130_scl_9T_phyCells.lef"),
                    gds_file=os.path.join(SKY130_SCL, "sky130_scl_9T_tech/gds/sky130_scl_9T_tech.gds"),
                    provides=[Provide(lib_type="technology")],
                ),
            ]
        else:
            raise ValueError(f"Incorrect standard cell library selection: {slib}")
        
        
        # Use SRAM22, only when the macros are installed
        self.use_sram22 = bool(SRAM22) and os.path.exists(SRAM22)
        if self.use_sram22:
            libs += [
                Library(
                    spice_file=os.path.join(SRAM22, "sram22.spice"),
                    provides=[Provide(lib_type="technology")],
                ),
            ]
        
        # Stdcell library-dependent lists
        stackups = []  # type: List[Stackup]
        phys_only = []  # type: List[Cell]
        dont_use = []  # type: List[Cell]
        spcl_cells = []  # type: List[SpecialCell]
        
        # base path -> list of corners
        lib_corner_files = {}
        
        if slib == "sky130_fd_sc_hd":
            phys_only = [
                "sky130_fd_sc_hd__fill_1",
                "sky130_fd_sc_hd__fill_2",
                "sky130_fd_sc_hd__fill_4",
                "sky130_fd_sc_hd__fill_8",
                "sky130_fd_sc_hd__tap_1",
                "sky130_fd_sc_hd__tap_2",
                "sky130_fd_sc_hd__tapvgnd_1",
                "sky130_fd_sc_hd__tapvpwrvgnd_1",
                "sky130_fd_sc_hd__diode_2",
            ]
            
            dont_use = [
                "*sdf*",
                "sky130_fd_sc_hd__probe_p_*",
                "sky130_fd_sc_hd__probec_p_*",
            ]
            
            spcl_cells = [
                # for now, skipping the tiecell step. I think the extracted verilog netlist is ignoring the tiecells which is causing lvs issues
                # TODO(jimfang) is this during extraction of verilog after par? or something done here/hacked up here? sometimes lvs doesn't fully include phycells.. is this is misconfig of some kind?
                SpecialCell(
                    cell_type=CellType("tiehilocell"), name=["sky130_fd_sc_hd__conb_1"]
                ),
                SpecialCell(
                    cell_type=CellType("tiehicell"),
                    name=["sky130_fd_sc_hd__conb_1"],
                    output_ports=["HI"],
                ),
                SpecialCell(
                    cell_type=CellType("tielocell"),
                    name=["sky130_fd_sc_hd__conb_1"],
                    output_ports=["LO"],
                ),
                SpecialCell(
                    cell_type=CellType("endcap"), name=["sky130_fd_sc_hd__tap_1"]
                ),
                SpecialCell(
                    cell_type=CellType("tapcell"),
                    name=["sky130_fd_sc_hd__tapvpwrvgnd_1"],
                ),
                SpecialCell(
                    # sky130_ef_sc_hd__fill_4 etc also exist; we don't use them; this is intentional.
                    # If you are running into LI density (too much) errors, consider using the sky130_ef_sc_hd__ cells
                    # see: https://github.com/fossi-foundation/open-pdks/tree/main/sky130/custom/sky130_fd_sc_hd#readme
                    cell_type=CellType("stdfiller"),
                    name=[
                        "sky130_fd_sc_hd__fill_1",
                        "sky130_fd_sc_hd__fill_2",
                        "sky130_fd_sc_hd__fill_4",
                        "sky130_fd_sc_hd__fill_8",
                    ],
                ),
                SpecialCell(
                    cell_type=CellType("decap"),
                    name=[
                        "sky130_fd_sc_hd__decap_3",
                        "sky130_fd_sc_hd__decap_4",
                        "sky130_fd_sc_hd__decap_6",
                        "sky130_fd_sc_hd__decap_8",
                        "sky130_fd_sc_hd__decap_12",
                    ],
                ),
                SpecialCell(
                    cell_type=CellType("driver"),
                    name=["sky130_fd_sc_hd__buf_4"],
                    input_ports=["A"],
                    output_ports=["X"],
                ),
                # this breaks synthesis with a complaint about "Cannot perform synthesis because libraries do not have usable inverters." from Genus.
                # note that innovus still recognizes and uses this cell as a buffer
                # TODO(jimfang) https://github.com/ucb-bar/hammer/blob/516aedb55385e9e3d9237738db5c8c2978e6475c/hammer/synthesis/genus/__init__.py#L337 - wrong tcl call for newer innovus versions?
                SpecialCell(
                    cell_type=CellType("ctsbuffer"), name=["sky130_fd_sc_hd__clkbuf_1"]
                ),
                
                # TODO(jimfang) below newly added
                SpecialCell(
                    cell_type=CellType("ctsinverter"),
                    name=[
                        "sky130_fd_sc_hd__clkinv_1",
                        "sky130_fd_sc_hd__clkinv_2",
                        "sky130_fd_sc_hd__clkinv_4",
                        "sky130_fd_sc_hd__clkinv_8",
                        "sky130_fd_sc_hd__clkinv_16"
                    ]
                ),
                
                SpecialCell(
                    cell_type=CellType("ctsgate"),
                    name=[
                        "sky130_fd_sc_hd__dlclkp_1",
                        "sky130_fd_sc_hd__dlclkp_2",
                        "sky130_fd_sc_hd__dlclkp_4"
                    ]
                )
            ]

            # Generate standard cell library
            library = slib
            
            # TODO(jimfang) - i dont think this is needed?
            # # scl vs 130a have different site names
            # sites = None
            
            # grab corners
            STDCELL_LIBRARY_BASE_PATH = os.path.join(SKY130A, "libs.ref", library)
            lib_corner_files[STDCELL_LIBRARY_BASE_PATH] = os.listdir(
                os.path.join(STDCELL_LIBRARY_BASE_PATH, "lib")
            )
            lib_corner_files[STDCELL_LIBRARY_BASE_PATH].sort()
            
            
            tlef_path = os.path.join(
                SKY130A, "libs.ref", library, "techlef", f"{library}__min.tlef"
            )
            metals = list(
                map(lambda m: Metal.model_validate(m), LEFUtils.get_metals(tlef_path))
            )
            stackups.append(
                Stackup(name=slib, grid_unit=Decimal("0.001"), metals=metals)
            )

            sites = [
                Site(name="unithd", x=Decimal("0.46"), y=Decimal("2.72")),
                Site(name="unithddbl", x=Decimal("0.46"), y=Decimal("5.44")),
            ]
            lvs_decks = [
                # sky130A uses NDA-ed or Cadence LVS decks as Hammer doesn't have klayout LVS support as of July 2026
                LVSDeck(
                    tool_name="calibre",
                    deck_name="calibre_lvs",
                    path="$SKY130_NDA/s8/V2.0.1/LVS/Calibre/lvsRules_s8",
                ),
                LVSDeck(
                    tool_name="pegasus",
                    deck_name="pegasus_lvs",
                    path=self.get_setting("technology.sky130.lvs_deck"),
                ),
            ]
            drc_decks = [
                DRCDeck(
                    tool_name="calibre",
                    deck_name="calibre_drc",
                    path="$SKY130_NDA/s8/V2.0.1/DRC/Calibre/s8_drcRules",
                ),
                DRCDeck(
                    tool_name="klayout",
                    deck_name="klayout_drc",
                    path="$SKY130A/libs.tech/klayout/drc/sky130A.lydrc",
                ),
                DRCDeck(
                    tool_name="pegasus",
                    deck_name="pegasus_drc",
                    path=self.get_setting("technology.sky130.drc_deck"),
                ),
            ]
        
        elif slib == "sky130_scl":
            # cadence standard cells & PDK do not provide IO cells; we'll use IO cells provided here: https://github.com/fossi-foundation/open-pdks/tree/main/sky130 -- see notes at line `if "sky130_fd_io" in library_base_path:`
            
            phys_only = [
                "FILL1",
                "FILL2",
                "FILL4",
                "FILL8",
                "FILL16",
                "FILL32",
                "FILL64",
                # FILL_DECAP8/16 are deliberately not physical-only: they contain devices,
                # so dropping them from the LVS netlist would break LVS (same as the hd decaps)
                "ANTENNA"
            ]
            
            dont_use = [
                
            ]
            
            spcl_cells = [
                SpecialCell(
                    cell_type=CellType("tiehicell"), name=["TIEHI"], input_ports=["Y"]
                ),
                SpecialCell(
                    cell_type=CellType("tielocell"), name=["TIELO"], input_ports=["Y"]
                ),
                # cadence sky130 std cells don't have a endcap TODO(jimfang) is this intended?
                # cadnece sky130 std cells contain taps by default (not a tapless design unlike the sky130A cells)
                
                # TODO(jimfang) below newly added
                SpecialCell(
                    cell_type=CellType("stdfiller"),
                    name=[
                        "FILL1",
                        "FILL2",
                        "FILL4",
                        "FILL8",
                        "FILL16",
                        "FILL32",
                        "FILL64",
                    ],
                ),
                
                SpecialCell(
                    cell_type=CellType("decap"),
                    name=[
                        "FILL_DECAP8",
                        "FILL_DECAP16",
                    ],
                ),
                
                SpecialCell(
                    cell_type=CellType("driver"),
                    name=[
                        "BUFX2",
                        "BUFX4",
                        "BUFX8",
                        "BUFX16",
                    ],
                ),
                
                SpecialCell(
                    cell_type=CellType("ctsbuffer"), name=[
                        "CLKBUFX2",
                        "CLKBUFX4",
                        "CLKBUFX8"
                    ]
                ),
                
                SpecialCell(
                    cell_type=CellType("ctsinverter"), name=[
                        "CLKINVX1",
                        "CLKINVX2",
                        "CLKINVX4",
                        "CLKINVX8",
                    ]
                ),
                
                SpecialCell(
                    cell_type=CellType("ctsgate"), name=[
                        "ICGX1"
                    ]
                ),
                
            ]
            
            
            # Generate standard cell library
            library = slib
            
            # grab corners
            # TODO(jimfang) can this process a lib.gz directly or do we need to unzip it when installing the PDK?
            STDCELL_LIBRARY_BASE_PATH = os.path.join(SKY130_SCL, "sky130_scl_9T")
            lib_corner_files[STDCELL_LIBRARY_BASE_PATH] = os.listdir(
                os.path.join(STDCELL_LIBRARY_BASE_PATH, "lib")
            )
            lib_corner_files[STDCELL_LIBRARY_BASE_PATH].sort()
            
            # Generate stackup
            metals = []  # type: List[Metal]

            tlef_path = os.path.join(SKY130_SCL, "sky130_scl_9T_tech", "lef", f"{slib}_9T.tlef")
            metals = list(
                map(lambda m: Metal.model_validate(m), LEFUtils.get_metals(tlef_path))
            )
            
            # The Cadence SCL routing pitches are larger than min_width + DRC min_spacing.
            # Hammer's TWWT power-strap generator requires its first spacing rule to
            # represent the track-aligned spacing: pitch - min_width.
            for metal in metals:
                track_aligned_spacing = metal.pitch - metal.min_width

                metal.power_strap_widths_and_spacings = [
                    rule.model_copy(
                        update={
                            "min_spacing": max(rule.min_spacing,
                            track_aligned_spacing)
                        }
                    )
                    for rule in metal.power_strap_widths_and_spacings
                ]
            
            stackups.append(
                Stackup(name=slib, grid_unit=Decimal("0.001"), metals=metals)
            )

            sites = [
                Site(name="CoreSite", x=Decimal("0.46"), y=Decimal("4.14")),
                Site(name="IOSite", x=Decimal("1.0"), y=Decimal("240.0")),
                Site(name="CornerSite", x=Decimal("240.0"), y=Decimal("240.0")),
            ]

            lvs_decks = [
                LVSDeck(
                    tool_name="pegasus",
                    deck_name="pegasus_lvs",
                    path=self.get_setting("technology.sky130.lvs_deck"),
                )
            ]
            drc_decks = [
                DRCDeck(
                    tool_name="calibre",
                    deck_name="calibre_drc",
                    path="$SKY130_NDA/s8/V2.0.1/DRC/Calibre/s8_drcRules",
                ),
                DRCDeck(
                    tool_name="pegasus",
                    deck_name="pegasus_drc",
                    path=self.get_setting("technology.sky130.drc_deck"),
                ),
            ]
        
        else:
            raise ValueError(f"Incorrect standard cell library selection: {slib}")
        
        
        # add skywater io cells to corners
        io_library_base_path = os.path.join(SKY130A, "libs.ref", "sky130_fd_io") # TODO(jimfang) see commentary near SKY130_IO variable definition
        if os.path.exists(io_library_base_path):
            # sorted: several IO libs can define the same cell for one corner, and the
            # tools bind whichever comes first, so the order must not depend on the filesystem
            lib_corner_files[io_library_base_path] = sorted(os.listdir(
                os.path.join(io_library_base_path, "lib")
            ))

        # IO GDS files registered with every IO corner below (see the sky130_ef_io notes
        # there). Older sky130A builds lack some of them (BWRC's 2024 install has no
        # connect_vdda_vddio_and_vssa_vssio_slice_20um), and Hammer requires every listed
        # GDS to exist when it writes the par script, so keep only the ones installed.
        io_gds_files = [
            "sky130_ef_io__analog.gds",
            "sky130_ef_io__disconnect_vccd_slice_5um.gds",
            "sky130_ef_io__gpiov2_pad_wrapped.gds",
            "sky130_ef_io__bare_pad.gds",
            "sky130_ef_io__disconnect_vdda_slice_5um.gds",
            "sky130_ef_io__connect_vcchib_vccd_and_vswitch_vddio_slice_20um.gds",
            "sky130_ef_io__connect_vdda_vddio_and_vssa_vssio_slice_20um.gds",
            "sky130_ef_io.gds",
        ]
        if os.path.exists(io_library_base_path):
            missing_io_gds = [f for f in io_gds_files
                              if not os.path.isfile(os.path.join(io_library_base_path, "gds", f))]
            if missing_io_gds:
                self.logger.warning("sky130A sky130_fd_io has no " + ", ".join(missing_io_gds)
                                    + ": those cells are not merged into the output GDS")
            io_gds_files = [f for f in io_gds_files if f not in missing_io_gds]

        # process corners & register them w/ hammer
        for library_base_path, cornerfiles in lib_corner_files.items():
            # print("TECH DEBUG base path: ", library_base_path)
            for cornerfilename in cornerfiles:
                # print("TECH DEBUG corner file: ", cornerfilename)
                if "sky130" not in cornerfilename or "#" in cornerfilename:
                    # cadence doesn't use the lib name in their corner libs
                    # also skip random temp files sometimes included in sky130_scl
                    continue
                if "ccsnoise" in cornerfilename:
                    continue  # ignore duplicate corner.lib/corner_ccsnoise.lib files

                tmp = cornerfilename.replace(".lib", "").strip("_nldm")
                if tmp + "_ccsnoise.lib" in lib_corner_files:
                    cornerfilename = (
                        tmp + "_ccsnoise.lib"
                    )  # use ccsnoise version of lib file
                    

                # keep this so that for fd_io, speed/temp/ is processed
                if "sky130A" in library_base_path:
                    split_cell_corner = re.split("_(ff)|_(ss)|_(tt)", tmp)
                    cell_name = split_cell_corner[0]
                    library = tmp.split("__")[0]
                    process = split_cell_corner[1:-1]
                    temp_volt = split_cell_corner[-1].split("_")[1:]

                    if not any(c is not None for c in process):
                        continue

                    # Filter out cross corners (e.g ff_ss or ss_ff)
                    # ignores lib files that has mixed corners such as _ff_ss; if file name is _ss_ss or __tt_tt, reduces to ss or tt.
                    if len(process) > 3:
                        if not functools.reduce(
                            lambda x, y: x and y,
                            map(lambda p, q: p == q, process[0:3], process[4:]),
                            True,
                        ):
                            continue
                    # Determine actual corner
                    speed = next(c for c in process if c is not None).replace("_", "")
                    temp = temp_volt[0]
                    temp = temp.replace("n", "-")
                    temp = temp.split("C")[0] + " C"

                    vdd = (".").join(temp_volt[1].split("v")) + " V"
                    if speed == "ff":
                        speed = "fast"
                    elif speed == "tt":
                        speed = "typical"
                    elif speed == "ss":
                        speed = "slow"
                    else:
                        self.logger.info(
                            "Skipping lib with unsupported corner: {}".format(speed)
                        )
                        continue
                else: 
                    library = "sky130_scl_9T" # TODO(jimfang) support other variants (compact/high speed) of the cdns 9T std cell lib
                    _, speed, vdd, temp, _ = tmp.split("_")

                    # force equivalent operating conditions for speed, since they're different between sky130a and scl
                    # this is needed unless someone regenerates lib characterization for the IO cells at the cadence corners
                    if speed == "ff":
                        temp = "-40 C"
                        vdd = "1.95 V"
                        speed = "fast"
                    if speed == "tt":
                        vdd = "1.80 V"
                        temp = "25 C"
                        speed = "typical"
                    if speed == "ss":
                        vdd = "1.60 V"
                        speed = "slow"
                        temp = "100 C"

                cdl_path = os.path.join(library_base_path, "cdl", library + ".cdl")
                spice_path = os.path.join(
                    library_base_path, "spice", library + ".spice"
                )

                # just prioritize spice, arbitrary choice
                # assert not (os.path.exists(cdl_path) and os.path.exists(spice_path)), "both spice and cdl netlists exist! this is ambiguous :("
                netlist_path = spice_path if os.path.exists(spice_path) else cdl_path

                if slib == "sky130_fd_sc_hd": 
                    lib_entry = Library(
                        nldm_liberty_file=os.path.join(
                            library_base_path, "lib", cornerfilename
                        ),
                        verilog_sim=os.path.join(
                            library_base_path,
                            "verilog",
                            library + ".v" # sky130_fd_sc_hd.v
                        ),
                        lef_file = (
                            "cache/sky130_ef_io.lef"
                            if library == "sky130_ef_io"
                            else os.path.join(library_base_path, "lef", library + ".lef")
                        ), # we modify sky130_ef_io.lef in setup_io_lefs(), so use the modified version if we are adding sky130_ef_io collateral
                        spice_file=netlist_path,
                        gds_file=os.path.join(library_base_path, "gds", library + ".gds"),
                        corner=Corner(nmos=speed, pmos=speed, temperature=temp),
                        supplies=Supplies(VDD=vdd, GND="0 V"),
                        provides=[Provide(lib_type="stdcell", vt="RVT")], # RVT = SVT
                    )
                    libs.append(lib_entry)
                    
                elif slib == "sky130_scl":
                    lib_entry = Library(
                        nldm_liberty_file=os.path.join(
                            library_base_path, "lib", cornerfilename
                        ),
                        verilog_sim=os.path.join(
                            library_base_path,
                            "verilog",
                            library + ".v" # sky130_scl_9T.v
                        ),
                        lef_file = (
                            "cache/sky130_ef_io.lef"
                            if library == "sky130_ef_io"
                            else os.path.join(library_base_path, "lef", library + ".lef")
                        ), # we modify sky130_ef_io.lef in setup_io_lefs(), so use the modified version if we are adding sky130_ef_io collateral
                        spice_file=netlist_path,
                        gds_file=os.path.join(library_base_path, "gds", library + ".gds"),
                        corner=Corner(nmos=speed, pmos=speed, temperature=temp),
                        supplies=Supplies(VDD=vdd, GND="0 V"),
                        provides=[Provide(lib_type="stdcell", vt="RVT")], # RVT = SVT
                    )
                    libs.append(lib_entry)
                
                if "sky130_fd_io" in library_base_path: # ex: /scratch/jfx/open-pdks/sky130/sky130A/libs.ref/sky130_fd_io
                    # these are seperate from the io gds
                    # the story is:
                    # sky130_fd_io is the skywater provided base I/O library
                    # sky130_ef_io is the efabless/open_pdks (== sky130A) addendum, which does the following:
                        # (1) Changes the orientation of the corner pad from upper-right to
                        #     lower-left with a wrapper cell called "sky130_fd_io__corner_pad".  Also
                        #     extends the power buses to make the dimensions of the corner pad
                        #     multiples of 1um.
                        #
                        # (2) Adds a 1um-wide spacer cell to complement the existing 5um-wide
                        #     spacer cell.
                        #
                        # (3) Adds wrappers for all the combinations of power pad base cell +
                        #     power pad overlay, to create all 12 combinations, for pads with
                        #     either high- or low-voltage clamps, connecting to one of the six
                        #     power domains vddio, vdda, vccd, vssio, vssa, or vssd.
                        #
                        # (4) Fix a DRC error in the SkyWater GPIO pad layout
                        #
                        # Source: https://github.com/fossi-foundation/open-pdks/tree/main/sky130/custom/sky130_fd_io
                    # sky130_ef_io instantiates/uses the underlying sky130_fd_io collateral (https://github.com/fossi-foundation/skywater-pdk-libs-sky130_fd_io), which depends on the primative devices library (https://github.com/fossi-foundation/skywater-pdk-libs-sky130_fd_pr)
                    # therefore, in order to use these IO cells, at least the above 2 should be installed. As a safety, we should just install all of OpenPDKs
                    
                    for extra_gds_file in io_gds_files:
                        lib_entry = Library(
                            nldm_liberty_file=os.path.join(
                                io_library_base_path, "lib", cornerfilename # TODO(jimfang) this as far as i can tell imports all avail lib files under sky130_fd_io... is this going to cause conflicts?
                            ),
                            verilog_sim=os.path.join(
                                io_library_base_path,
                                "verilog",
                                "sky130_fd_io.v"
                            ),
                            lef_file=os.path.join(
                                io_library_base_path, "lef", "sky130_fd_io.lef"
                            ),
                            spice_file=os.path.join(io_library_base_path, "spice", "sky130_fd_io.spice"),
                            gds_file=os.path.join(
                                io_library_base_path, "gds", extra_gds_file
                            ),
                            corner=Corner(nmos=speed, pmos=speed, temperature=temp),
                            supplies=Supplies(VDD=vdd, GND="0 V"),
                            provides=[Provide(lib_type="iocell", vt="RVT")],
                        )
                        libs.append(lib_entry)
                        
                    # add ef_io cell's lef files (one time is enough since this isn't specific to the particular gds)
                    lib_entry = Library(
                        lef_file="cache/sky130_ef_io.lef",
                        provides=[Provide(lib_type="iocell", vt="RVT")],
                    )
                    libs.append(lib_entry)
        
        if slib == "sky130_fd_sc_hd":
            # add primitives.v which sky130_fd_sc_hd depends on (once only since it only needs to be included once to be able to be referenced)
            lib_entry = Library(
                verilog_sim=os.path.join(
                    SKY130A, "libs.ref", "sky130_fd_sc_hd", "verilog", "primitives.v"
                ),
                provides=[Provide(lib_type="stdcell", vt="RVT")], # RVT = SVT
            )
            libs.append(lib_entry)

        # An explicit technology.sky130.drc_deck_sources list (SledgeHammer's
        # earlier deck scheme) overrides the per-library defaults above.
        drc_decks = self._drc_decks_from_sources() or drc_decks

        self.config = TechConfig(
            name="Skywater 130nm Library",
            grid_unit="0.001",
            shrink_factor=None,
            installs=[
                PathPrefix(id="$SKY130_NDA", path="technology.sky130.sky130_nda"),
                PathPrefix(id="$SKY130A", path="technology.sky130.sky130A"),
                PathPrefix(id="$SKY130_CDS", path="technology.sky130.sky130_cds"),
                PathPrefix(id="$SKY130_SCL", path="technology.sky130.sky130_scl"),
            ],
            libraries=libs,
            gds_map_file="sky130_lefpin.map",
            physical_only_cells_list=phys_only,
            dont_use_list=dont_use,
            additional_drc_text="",
            lvs_decks=lvs_decks,
            drc_decks=drc_decks,
            additional_lvs_text="",
            tarballs=None,
            sites=sites,
            stackups=stackups,
            special_cells=spcl_cells,
            extra_prefixes=None,
        )

        self.library_name = slib
        
    
    def _drc_decks_from_sources(self) -> List[DRCDeck]:
        """DRC decks from technology.sky130.drc_deck_sources: one deck per entry,
        for whichever DRC tool is selected. Empty when the list is unset or
        empty, in which case the per-library defaults in gen_config apply.
        """
        sources = self.get_setting("technology.sky130.drc_deck_sources") or []
        if not sources:
            return []
        tool = self.get_setting("vlsi.core.drc_tool").replace("hammer.drc.", "")
        return [
            DRCDeck(tool_name=tool, deck_name=f"{tool}_drc", path=path)
            for path in sources
        ]

    def post_install_script(self) -> None:
        # check whether variables were overriden to point to a valid path
        if self.get_setting("technology.sky130.stdcell_library") == "sky130_fd_sc_hd":
            self.setup_cdl()
            self.setup_verilog()
        self.setup_techlef()
        # gen_config treats the IO library as optional; so does this
        if os.path.exists(os.path.join(self.get_setting("technology.sky130.sky130A"), "libs.ref", "sky130_fd_io")):
            self.setup_io_lefs()
        else:
            self.logger.warning("sky130A has no libs.ref/sky130_fd_io: IO cells are unavailable")
        # self.setup_calibre_lvs_deck()
        self.setup_hvl_ls_lef()
        self.logger.info('Loaded Sky130 Tech')


    def setup_cdl(self) -> None:
        """Copy and hack the cdl, replacing pfet_01v8_hvt/nfet_01v8 with
        respective names in LVS deck
        """
        setting_dir = self.get_setting("technology.sky130.sky130A")
        setting_dir = Path(setting_dir)
        source_path = (
            setting_dir
            / "libs.ref"
            / self.library_name
            / "cdl"
            / f"{self.library_name}.cdl"
        )
        if not source_path.exists():
            raise FileNotFoundError(f"CDL not found: {source_path}")

        cache_tech_dir_path = Path(self.cache_dir)
        os.makedirs(cache_tech_dir_path, exist_ok=True)
        dest_path = cache_tech_dir_path / f"{self.library_name}.cdl"

        # device names expected in LVS decks
        lvs_tool = self.get_setting("vlsi.core.lvs_tool")
        if lvs_tool == "hammer.lvs.calibre":
            pmos = "phighvt"
            nmos = "nshort"
        elif lvs_tool == "hammer.lvs.pegasus":
            pmos = "pfet_01v8_hvt"
            nmos = "nfet_01v8"
        elif lvs_tool == "hammer.lvs.netgen":
            # The stock .spice already carries netgen's device names, including the
            # sky130_fd_pr__special_* devices that a substring rename would mangle, and
            # the core override would swap it for a cached CDL of the same name. So
            # cache none, and drop one that a run with another LVS tool left behind.
            if dest_path.exists():
                dest_path.unlink()
            return
        else:
            _copy_cache_file(source_path, dest_path)
            return

        self.logger.info(
            "Modifying CDL netlist: {} -> {}".format(source_path, dest_path)
        )
        out = ["*.SCALE MICRON\n"]
        with open(source_path, "r") as sf:
            for line in sf:
                line = line.replace("pfet_01v8_hvt", pmos)
                line = line.replace("nfet_01v8", nmos)
                if lvs_tool == "hammer.lvs.pegasus":
                    # The Cadence decks define no special_* MOS devices: extraction reports
                    # those transistors (in 67 flop, latch and clock-gate cells) as plain
                    # nfet_01v8 / pfet_01v8_hvt, so the schematic has to name them that way.
                    line = re.sub(r"\bspecial_(nfet_01v8|pfet_01v8_hvt)\b", r"\1", line)
                out.append(line)
        _write_cache_file(dest_path, "".join(out))

    # Copy and hack the verilog
    #   - <library_name>.v: remove 'wire 1' and one endif line to fix syntax errors
    #   - primitives.v: set default nettype to 'wire' instead of 'none'
    #           (the open-source RTL sim tools don't treat undeclared signals as errors)
    #   - Deal with numerous inconsistencies in timing specify blocks.
    def setup_verilog(self) -> None:
        setting_dir = self.get_setting("technology.sky130.sky130A")
        setting_dir = Path(setting_dir)

        # <library_name>.v
        source_path = (
            setting_dir
            / "libs.ref"
            / self.library_name
            / "verilog"
            / f"{self.library_name}.v"
        )
        if not source_path.exists():
            raise FileNotFoundError(f"Verilog not found: {source_path}")

        cache_tech_dir_path = Path(self.cache_dir)
        os.makedirs(cache_tech_dir_path, exist_ok=True)
        dest_path = cache_tech_dir_path / f"{self.library_name}.v"

        self.logger.info(
            "Modifying Verilog netlist: {} -> {}".format(source_path, dest_path)
        )
        out = []
        with open(source_path, "r") as sf:
            for line in sf:
                line = line.replace("wire 1", "// wire 1")
                line = line.replace(
                    "`endif SKY130_FD_SC_HD__LPFLOW_BLEEDER_FUNCTIONAL_V",
                    "`endif // SKY130_FD_SC_HD__LPFLOW_BLEEDER_FUNCTIONAL_V",
                )
                out.append(line)
        _write_cache_file(dest_path, "".join(out))

        # primitives.v
        source_path = (
            setting_dir / "libs.ref" / self.library_name / "verilog" / "primitives.v"
        )
        if not source_path.exists():
            raise FileNotFoundError(f"Verilog not found: {source_path}")

        cache_tech_dir_path = Path(self.cache_dir)
        os.makedirs(cache_tech_dir_path, exist_ok=True)
        dest_path = cache_tech_dir_path / "primitives.v"

        self.logger.info(
            "Modifying Verilog netlist: {} -> {}".format(source_path, dest_path)
        )
        with open(source_path, "r") as sf:
            text = sf.read()
        _write_cache_file(dest_path, text.replace("`default_nettype none", "`default_nettype wire"))

    # Copy and hack the tech-lef, adding this very important `licon` section
    # see comments near _additional_tlef_edit_for_scl section.
    def setup_techlef(self) -> None:
        cache_tech_dir_path = Path(self.cache_dir)
        os.makedirs(cache_tech_dir_path, exist_ok=True)
        if self.get_setting("technology.sky130.stdcell_library") == "sky130_fd_sc_hd":
            setting_dir = self.get_setting("technology.sky130.sky130A")
            setting_dir = Path(setting_dir)
            source_path = (
                setting_dir
                / "libs.ref"
                / self.library_name
                / "techlef"
                / f"{self.library_name}__nom.tlef"
            )
            dest_path = cache_tech_dir_path / f"{self.library_name}__nom.tlef"
        else:
            setting_dir = self.get_setting("technology.sky130.sky130_scl")
            setting_dir = Path(setting_dir)
            source_path = setting_dir / "sky130_scl_9T_tech" /  "lef" / "sky130_scl_9T.tlef"
            dest_path = cache_tech_dir_path / "sky130_scl_9T.tlef"
        if not source_path.exists():
            raise FileNotFoundError(f"Tech-LEF not found: {source_path}")

        self.logger.info(
            "Modifying Technology LEF: {} -> {}".format(source_path, dest_path)
        )
        with open(source_path, "r") as sf:
            text = sf.read()
        scl = self.get_setting("technology.sky130.stdcell_library") == "sky130_scl"
        if scl:
            anchor, edit = "END poly", _additional_tlef_edit_for_scl
        else:
            # newer open_pdks tech LEFs already define licon; add only what is missing
            anchor, edit = "END pwell", _missing_layer_blocks(_the_tlef_edit, text)
        out = []
        for line in text.splitlines(keepends=True):
            out.append(line)
            if edit and line.strip() == anchor:
                out.append(edit)
        _write_cache_file(dest_path, "".join(out))

    # Power pins for clamps must be CLASS CORE - annotation is needed by innovus to mark the all clamp's met3 power port to be intended for core-side connection (as opposed to ring side -- which is inferred by innovus in absence of this annotation)
    #     pad-ring side                         core side
    #     VDDIO/VSSIO ring ── clamp ── VCCD1/VSSD1 tails
    #     PORT                         PORT CLASS CORE
    # connect/disconnect spacers must be CLASS PAD SPACER, not AREAIO - known bug, see some fixes already commited here: https://github.com/fossi-foundation/open-pdks/commit/d815bb30c9afdf9e264c276a8a2b533108dea3d0
    # add ANTENNAGATEAREA 1.529 for met3 => total MOS gate-oxide area electrically connected to that PIN -- cannot confirm the source of 1.529 number.
    # def setup_io_lefs(self) -> None:
    #     sky130A_path = Path(self.get_setting("technology.sky130.sky130A"))
    #     source_path = (
    #         sky130A_path / "libs.ref" / "sky130_fd_io" / "lef" / "sky130_ef_io.lef"
    #     )
    #     if not source_path.exists():
    #         raise FileNotFoundError(f"IO LEF not found: {source_path}")

    #     cache_tech_dir_path = Path(self.cache_dir)
    #     os.makedirs(cache_tech_dir_path, exist_ok=True)
    #     dest_path = cache_tech_dir_path / "sky130_ef_io.lef"

    #     with open(source_path, "r") as sf:
    #         with open(dest_path, "w") as df:
    #             self.logger.info(
    #                 "Modifying IO LEF: {} -> {}".format(source_path, dest_path)
    #             )
    #             sl = sf.readlines()
    #             for net in ["VCCD1", "VSSD1", "VDDA", "VSSA", "VSSIO"]:
    #                 start = [idx for idx, line in enumerate(sl) if "PIN " + net in line]
    #                 end = [idx for idx, line in enumerate(sl) if "END " + net in line]
    #                 intervals = zip(start, end)
    #                 for intv in intervals:
    #                     port_idx = [
    #                         idx
    #                         for idx, line in enumerate(sl[intv[0] : intv[1]])
    #                         if "PORT" in line and "met3" in sl[intv[0] + idx + 1]
    #                     ]
    #                     for idx in port_idx:
    #                         sl[intv[0] + idx] = sl[intv[0] + idx].replace(
    #                             "PORT", "PORT\n      CLASS CORE ;"
    #                         )
    #             # force class to spacer
    #             for macro_name in [
    #                 "sky130_ef_io__disconnect_vccd_slice_5um",
    #                 "sky130_ef_io__disconnect_vdda_slice_5um",
    #                 "sky130_ef_io__connect_vcchib_vccd_and_vswitch_vddio_slice_20um",
    #             ]:
    #                 start = [
    #                     idx
    #                     for idx, line in enumerate(sl)
    #                     if f"MACRO {macro_name}" in line
    #                 ]
    #                 sl[start[0] + 1] = sl[start[0] + 1].replace("AREAIO", "SPACER")

    #             for idx, line in enumerate(sl):
    #                 if "PIN OUT" in line:
    #                     sl[idx + 1].replace(
    #                         "DIRECTION INPUT ;",
    #                         "DIRECTION INPUT ;\n    ANTENNAGATEAREA 1.529 LAYER met3 ;",
    #                     )

    #             df.writelines(sl)
    
    def setup_io_lefs(self) -> None:
        """
        Prepare a modified sky130_ef_io LEF for Innovus.

        Modifications:
        1. Mark met3 PORTs of selected dedicated PAD POWER pins as CLASS CORE.
        Innovus sRoute uses this annotation to distinguish the
        core-facing power ports from the pad-ring-facing ports.

        This applies to both clamped and non-clamped dedicated power pads,
        but NOT to arbitrary IO macros, GPIOs, spacers, corner cells, etc.

            pad-ring side                         core side
            M4/M5 IO buses -- power pad -- M3 power tail
            ordinary PORT               PORT CLASS CORE

        2. Force selected connect/disconnect spacer cells to
        CLASS PAD SPACER instead of CLASS PAD AREAIO.

        3. Add ANTENNAGATEAREA 1.529 LAYER met3 to PIN OUT entries.

        The resulting LEF is written into the Hammer technology cache.
        """

        sky130A_path = Path(self.get_setting("technology.sky130.sky130A"))
        source_path = (
            sky130A_path
            / "libs.ref"
            / "sky130_fd_io"
            / "lef"
            / "sky130_ef_io.lef"
        )

        if not source_path.exists():
            raise FileNotFoundError(f"IO LEF not found: {source_path}")

        cache_tech_dir_path = Path(self.cache_dir)
        os.makedirs(cache_tech_dir_path, exist_ok=True)

        dest_path = cache_tech_dir_path / "sky130_ef_io.lef"

        self.logger.info(
            "Modifying IO LEF: {} -> {}".format(source_path, dest_path)
        )

        with open(source_path, "r") as sf:
            sl = sf.readlines()

        source_core_count = "".join(sl).count("CLASS CORE ;")
        self.logger.info(
            "IO LEF source CLASS CORE count: {}".format(source_core_count)
        )

        # ------------------------------------------------------------------
        # Identify dedicated CLASS PAD POWER macros.
        #
        # Do not restrict this to "_clamped" macros: non-clamped dedicated
        # supply pads also have core-facing met3 power ports which need the
        # same CLASS CORE annotation.
        #
        # At the same time, do not apply the modification to every occurrence
        # of VDDIO/VSSIO/VCCD/etc. in the entire IO library. Only dedicated
        # PAD POWER macros are eligible.
        # ------------------------------------------------------------------

        pad_power_macros = set()

        current_macro = None

        for line in sl:
            stripped = line.strip()

            if stripped.startswith("MACRO "):
                parts = stripped.split()
                current_macro = parts[1] if len(parts) >= 2 else None
                continue

            if (
                current_macro is not None
                and stripped == "CLASS PAD POWER ;"
            ):
                pad_power_macros.add(current_macro)
                continue

            if (
                current_macro is not None
                and stripped == f"END {current_macro}"
            ):
                current_macro = None

        self.logger.info(
            "IO LEF: found {} CLASS PAD POWER macros".format(
                len(pad_power_macros)
            )
        )

        # ------------------------------------------------------------------
        # Mark core-facing met3 PG ports as CLASS CORE.
        #
        # Selection is deliberately constrained to:
        #
        #   1. Macro is CLASS PAD POWER.
        #   2. Exact PIN name is one of the known supply pins below.
        #   3. The individual PORT contains met3 geometry.
        #
        # This means:
        #
        #   dedicated VDDIO/VSSIO/VCCD/VSSD/etc. pads  -> eligible
        #   clamped dedicated supply pads              -> eligible
        #   non-clamped dedicated supply pads          -> eligible
        #   GPIO / signal pads                         -> excluded
        #   com_bus/connect/disconnect spacers          -> excluded
        #   corner/endcap cells                        -> excluded
        #
        # Exact PIN matching is important. For example, "VSSIO" must not
        # accidentally match VSSIO_Q.
        # ------------------------------------------------------------------

        core_pin_names = {
            "VCCD",
            "VCCD1",
            "VSSD",
            "VSSD1",
            "VDDA",
            "VSSA",
            "VDDIO",
            "VSSIO",
        }

        core_ports_added = 0
        current_macro = None

        i = 0
        while i < len(sl):
            stripped = sl[i].strip()

            # Track which macro this PIN belongs to.
            if stripped.startswith("MACRO "):
                parts = stripped.split()
                current_macro = parts[1] if len(parts) >= 2 else None
                i += 1
                continue

            if (
                current_macro is not None
                and stripped == f"END {current_macro}"
            ):
                current_macro = None
                i += 1
                continue

            # Match an exact LEF PIN declaration.
            if stripped.startswith("PIN "):
                parts = stripped.split()
                pin_name = parts[1] if len(parts) >= 2 else None

                if (
                    current_macro in pad_power_macros
                    and pin_name in core_pin_names
                ):
                    # Find the exact END <pin_name> corresponding to this PIN.
                    pin_end = i + 1

                    while pin_end < len(sl):
                        if sl[pin_end].strip() == f"END {pin_name}":
                            break
                        pin_end += 1

                    if pin_end >= len(sl):
                        raise RuntimeError(
                            f"Malformed IO LEF: could not find END {pin_name} "
                            f"after line {i + 1} in macro {current_macro}"
                        )

                    # Walk through each PORT belonging to this PIN.
                    j = i + 1

                    while j < pin_end:
                        if sl[j].strip() != "PORT":
                            j += 1
                            continue

                        # Locate the END of this PORT.
                        port_end = j + 1

                        while port_end < pin_end:
                            if sl[port_end].strip() == "END":
                                break
                            port_end += 1

                        if port_end >= pin_end:
                            raise RuntimeError(
                                f"Malformed IO LEF: unterminated PORT in "
                                f"{current_macro}/{pin_name} near line {j + 1}"
                            )

                        # A PORT may contain more than one LAYER statement.
                        # Mark it CORE if any of its geometry is on met3.
                        has_met3 = False
                        already_core = False

                        for n in range(j + 1, port_end):
                            s = sl[n].strip()

                            if s == "CLASS CORE ;":
                                already_core = True

                            if s.startswith("LAYER "):
                                layer_parts = s.split()

                                if len(layer_parts) >= 2:
                                    layer_name = layer_parts[1].rstrip(";")

                                    if layer_name == "met3":
                                        has_met3 = True

                        if has_met3 and not already_core:
                            # Preserve the PORT indentation and insert
                            # CLASS CORE immediately after PORT.
                            indent = sl[j][
                                : len(sl[j]) - len(sl[j].lstrip())
                            ]

                            sl[j] = (
                                f"{indent}PORT\n"
                                f"{indent}  CLASS CORE ;\n"
                            )

                            core_ports_added += 1

                            self.logger.debug(
                                "IO LEF: marked {}/{} met3 PORT "
                                "as CLASS CORE".format(
                                    current_macro,
                                    pin_name,
                                )
                            )

                        j = port_end + 1

                    i = pin_end

            i += 1

        self.logger.info(
            "IO LEF: added CLASS CORE to {} met3 PAD POWER PORTs".format(
                core_ports_added
            )
        )

        # ------------------------------------------------------------------
        # Force connect/disconnect cells to be PAD SPACER rather than AREAIO.
        #
        # Known Open-PDKs issue/fix:
        # https://github.com/fossi-foundation/open-pdks/commit/d815bb30c9afdf9e264c276a8a2b533108dea3d0
        # ------------------------------------------------------------------

        spacer_macro_names = [
            "sky130_ef_io__disconnect_vccd_slice_5um",
            "sky130_ef_io__disconnect_vdda_slice_5um",
            "sky130_ef_io__connect_vcchib_vccd_and_vswitch_vddio_slice_20um",
        ]

        for macro_name in spacer_macro_names:
            macro_start = None

            for idx, line in enumerate(sl):
                if line.strip() == f"MACRO {macro_name}":
                    macro_start = idx
                    break

            if macro_start is None:
                raise RuntimeError(
                    f"Could not find IO macro {macro_name} in {source_path}"
                )

            # CLASS is expected near the beginning of the macro, but search a
            # short distance rather than blindly assuming macro_start + 1.
            class_found = False

            for idx in range(
                macro_start + 1,
                min(macro_start + 10, len(sl)),
            ):
                stripped = sl[idx].strip()

                if stripped.startswith("CLASS "):
                    sl[idx] = sl[idx].replace("AREAIO", "SPACER")
                    class_found = True
                    break

                # Do not accidentally run into another macro.
                if stripped.startswith("MACRO "):
                    break

            if not class_found:
                raise RuntimeError(
                    f"Could not find CLASS statement for IO macro {macro_name}"
                )

        # ------------------------------------------------------------------
        # Add antenna gate area annotation to PIN OUT.
        #
        # The previous implementation called str.replace() without assigning
        # its return value, so the LEF was never actually modified.
        # ------------------------------------------------------------------

        antenna_count = 0

        for idx, line in enumerate(sl):
            if line.strip() != "PIN OUT":
                continue

            # Find the DIRECTION statement belonging to this PIN.
            pin_end = idx + 1

            while pin_end < len(sl):
                if sl[pin_end].strip() == "END OUT":
                    break
                pin_end += 1

            for direction_idx in range(idx + 1, pin_end):
                if sl[direction_idx].strip() == "DIRECTION INPUT ;":
                    indent = sl[direction_idx][
                        : len(sl[direction_idx])
                        - len(sl[direction_idx].lstrip())
                    ]

                    # Don't add a duplicate annotation if this source LEF
                    # already contains one.
                    already_has_antenna = any(
                        "ANTENNAGATEAREA 1.529 LAYER met3 ;" in sl[n]
                        for n in range(direction_idx + 1, pin_end)
                    )

                    if not already_has_antenna:
                        sl[direction_idx] = (
                            f"{indent}DIRECTION INPUT ;\n"
                            f"{indent}ANTENNAGATEAREA 1.529 LAYER met3 ;\n"
                        )
                        antenna_count += 1

                    break

        self.logger.info(
            "IO LEF: added ANTENNAGATEAREA to {} PIN OUT entries".format(
                antenna_count
            )
        )

        # ------------------------------------------------------------------
        # Write modified LEF.
        # ------------------------------------------------------------------

        # Validate the text before publishing it, not by re-reading the cache file:
        # parallel tasks sharing this cache may be rewriting it at the same moment.
        output_text = "".join(sl)
        _write_cache_file(dest_path, output_text)

        # ------------------------------------------------------------------
        # Validate the generated cache LEF.
        #
        # If this file contains CLASS CORE but the LEF copied into par-rundir
        # does not, the corruption/replacement occurred after this step.
        # ------------------------------------------------------------------

        output_core_count = output_text.count("CLASS CORE ;")

        self.logger.info(
            "IO LEF cache path: {}".format(dest_path)
        )
        self.logger.info(
            "IO LEF output CLASS CORE count: {}".format(output_core_count)
        )

        if output_core_count == 0:
            raise RuntimeError(
                "setup_io_lefs generated an IO LEF with zero "
                f"CLASS CORE annotations: {dest_path}"
            )

        if core_ports_added == 0 and source_core_count == 0:
            raise RuntimeError(
                "setup_io_lefs did not find any eligible met3 PAD POWER "
                "PORTs requiring CLASS CORE annotation"
            )

        # ------------------------------------------------------------------
        # Explicit validation of known core-facing power-pad geometries.
        #
        # These cover both the original VSSA failure and the later VDDIO/VCCD
        # failures seen in Innovus connectivity checking.
        # ------------------------------------------------------------------

        expected_core_ports = {
            "VSSA port 1": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 0.495 -2.035 24.395 30.480 ;"
            ),
            "VSSA port 2": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 50.390 -2.035 74.290 34.725 ;"
            ),
            "VDDIO port 1": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 0.495 -2.035 24.395 17.765 ;"
            ),
            "VDDIO port 2": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 50.390 -2.035 74.290 17.765 ;"
            ),
            "VCCD port 1": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 0.500 -0.035 24.500 6.865 ;"
            ),
            "VCCD port 2": (
                "PORT\n"
                "      CLASS CORE ;\n"
                "      LAYER met3 ;\n"
                "        RECT 50.755 -0.035 74.700 6.865 ;"
            ),
        }

        for description, expected_text in expected_core_ports.items():
            if expected_text not in output_text:
                raise RuntimeError(
                    "Generated IO LEF is missing CLASS CORE on known "
                    f"core-facing {description}"
                )

        self.logger.info(
            "IO LEF validation passed: known VSSA/VDDIO/VCCD "
            "core-facing met3 ports are CLASS CORE"
        )

    def setup_hvl_ls_lef(self) -> bool:
        # Treat HVL cells as if they were hard macros to avoid needing to set them
        # up "properly" with multiple power domains
        # does 2 things:
        # strips "SITE unithv ;" across all cells/MACROs
        # converts "CLASS CORE ;" -> "CLASS BLOCK ;" - hard macro each cell
        
        # iterating through all the cells is intentional, since recent PDK versions ship all the cells in 1 GDS file; if we only pulled out the level shifter lef into its own file, we would need to extract that 1 cell into a new GDS (would need to manually do this every PDK update, not sustainable).
        # We can include all the HVL collateral, but will only instantiate the level shifter in the hammer design yaml.

        # TODO(jimfang) BEFORE PROD: place edited level shifter collat in hammer plugin folder, then change this path
        misc_collateral = self.get_setting("technology.sky130.misc_tapeout_collateral")
        if misc_collateral:
            # level shifter with an added met1 access pad (cadence-skywater-130-pdk)
            source_path = Path(misc_collateral) / "OPEN-PDKS-Modifications" / "sky130_fd_sc_hvl__lsbufhv2lv_1_m1_access" / "sky130_fd_sc_hvl__lsbufhv2lv_1.lef"
        else:
            # stock sky130A library LEF, as before misc_tapeout_collateral existed
            source_path = Path(self.get_setting("technology.sky130.sky130A")) / "libs.ref" / "sky130_fd_sc_hvl" / "lef" / "sky130_fd_sc_hvl.lef"
        
        lef_name = "sky130_fd_sc_hvl__lsbufhv2lv_1.lef"
        cache_path = Path(self.cache_dir) / "fd_sc_hvl__lef" / lef_name # will be used in design.yaml & correlated to the sky130_fd_sc_hvl gds
        if not source_path.exists():
            if misc_collateral:
                raise FileNotFoundError(f"HVL level shifter LEF not found under technology.sky130.misc_tapeout_collateral: {source_path}")
            self.logger.warning(f"sky130A has no sky130_fd_sc_hvl LEF, skipping the level shifter LEF: {source_path}")
            return False

        self.logger.info(f"Patching HVL Level Shifter LEF: {source_path} -> {cache_path}")
        out = []
        with source_path.open("r") as sf:
            is_in_site_def = False
            is_in_macro_def = False
            for line in sf:
                if is_in_site_def:
                    if "END unithv" in line:
                        is_in_site_def = False
                elif not is_in_macro_def and "SITE unithv" in line:
                    is_in_site_def = True
                elif "MACRO " in line:
                    is_in_macro_def = True
                    out.append(line)
                elif "SITE unithv" in line:
                    pass
                else:
                    out.append(
                        line.replace("CLASS CORE", "CLASS BLOCK")
                        if not (("ANTENNACELL" in line) or ("SPACER" in line))
                        else line
                    )
        _write_cache_file(cache_path, "".join(out))
        return True
    
    def setup_calibre_lvs_deck(self) -> bool:
        # Remove conflicting specification statements found in PDK LVS decks
        pattern = ".*({}).*\n".format("|".join(LVS_DECK_SCRUB_LINES))
        matcher = re.compile(pattern)

        source_paths = self.get_setting("technology.sky130.lvs_deck_sources")
        lvs_decks = self.config.lvs_decks
        if not lvs_decks:
            return True
        for i, deck in enumerate(lvs_decks):
            if deck.tool_name != "calibre":
                continue
            try:
                source_path = Path(source_paths[i])
            except IndexError:
                self.logger.error(
                    "No corresponding source for LVS deck {}".format(deck)
                )
                continue
            if not source_path.exists():
                raise FileNotFoundError(f"LVS deck not found: {source_path}")
            cache_tech_dir_path = Path(self.cache_dir)
            dest_path = os.path.join(cache_tech_dir_path, os.path.basename(deck.path))
            with open(source_path, "r") as sf:
                with open(dest_path, "w") as df:
                    self.logger.info(
                        "Modifying LVS deck: {} -> {}".format(source_path, dest_path)
                    )
                    df.write(matcher.sub("", sf.read()))
                    df.write(LVS_DECK_INSERT_LINES)
        return True

    def get_tech_power_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        hooks = {}

        def enable_scl_clk_gating_cell_hook(ht: HammerTool) -> bool:
            ht.append(
                "set_db [get_db lib_cells -if {.base_name == ICGX1}] .avoid false"
            )
            return True

        # The clock gating cell is set to don't touch/use in the cadence pdk (as of v0.0.3), work around that
        if self.get_setting("technology.sky130.stdcell_library") == "sky130_scl":
            hooks["joules"] = [
                HammerTool.make_pre_insertion_hook(
                    "synthesize_design", enable_scl_clk_gating_cell_hook
                )
            ]

        return hooks.get(tool_name, [])

    def get_tech_syn_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        hooks = {}

        def enable_scl_clk_gating_cell_hook(ht: HammerTool) -> bool:
            ht.append("set_db [get_db lib_cells *ICGX1*] .avoid false")
            ht.append("set_db [get_db lib_cells *ICGX1*] .preserve false")
            # only where clock gating is wanted: clock_gating_mode "empty" disables inference
            if ht.get_setting("synthesis.clock_gating_mode") == "auto":
                ht.append("set_db / .lp_insert_clock_gating true")
            return True

        def scl_no_functional_scan_flops_hook(ht: HammerTool) -> bool:
            # Without this, Genus folds load-enable muxes into the scan flops' scan mux
            # (SDFFRX1 in place of DFFRX1 plus a mux), and Innovus 25.1 place_opt_design
            # then stops on scan flops that belong to no scan chain (IMPSP-9099).
            assert isinstance(ht, TCLTool), "Genus settings can only run on TCL tools"
            ht.append("set_db / .use_scan_seqs_for_non_dft false")
            return True

        # The clock gating cell is set to don't touch/use in the cadence pdk (as of v0.0.3), work around that
        if self.get_setting("technology.sky130.stdcell_library") == "sky130_scl":
            hooks["genus"] = [
                HammerTool.make_pre_insertion_hook(
                    "syn_generic", enable_scl_clk_gating_cell_hook
                ),
                HammerTool.make_pre_insertion_hook(
                    "syn_generic", scl_no_functional_scan_flops_hook
                ),
            ]

            # seems to mess up lvs for now
        # hooks['genus'].append(HammerTool.make_removal_hook("add_tieoffs"))
        return hooks.get(tool_name, [])

    def get_tech_par_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        hooks = {
            "innovus": [
                HammerTool.make_post_insertion_hook(
                    "init_design", sky130_innovus_settings
                ),
                HammerTool.make_pre_insertion_hook("power_straps", sky130_connect_nets),
                HammerTool.make_pre_insertion_hook(
                    "write_design", sky130_connect_nets2
                ),
            ]
        }
        # there are no cap/decap cells in the cadence stdcell library as of version 0.0.3, so we can't do things that reference them
        if self.get_setting("technology.sky130.stdcell_library") == "sky130_scl":
            hooks["innovus"].extend(
                [
                    HammerTool.make_pre_insertion_hook(
                        "power_straps", power_rail_straps_no_tapcells
                    ),
                    # TODO(jimfang) can we remove this now? if tools can recognize clock buffer cells
                    # HammerTool.make_pre_insertion_hook(
                    #     "clock_tree", set_cts_base_cells
                    # ),
                ]
            )
        else:
            hooks["innovus"].append(
                HammerTool.make_pre_insertion_hook(
                    "place_tap_cells", sky130_add_endcaps
                )
            )

        return hooks.get(tool_name, [])

    def get_tech_drc_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        calibre_hooks = []
        pegasus_hooks = []
        if self.get_setting("technology.sky130.drc_blackbox_srams"):
            calibre_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_drc_run_file", calibre_drc_blackbox_srams
                )
            )
            pegasus_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_drc_ctl_file", pegasus_drc_blackbox_srams
                )
            )
        # jim - removed 8/9/2025: previously had hook to pegasus_drc_blackbox_io_cells - we need to run drc on io cells since cadence will not black box io cells for signoff
        pegasus_hooks.append(
            HammerTool.make_post_insertion_hook(
                "generate_drc_ctl_file", false_rules_off
            )
        )
        # Excludes the IO cells from Pegasus DRC. Signoff has to check them
        # (Cadence will not black box IO cells), so a signoff driver removes this
        # step; the chipyard sky130 hammer-driver does, and needs it to exist.
        pegasus_hooks.append(
            HammerTool.make_post_insertion_hook(
                "generate_drc_ctl_file", pegasus_drc_blackbox_io_cells
            )
        )
        hooks = {"calibre": calibre_hooks, "pegasus": pegasus_hooks}
        return hooks.get(tool_name, [])

    def get_tech_lvs_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        calibre_hooks = []
        pegasus_hooks = []
        if self.use_sram22:
            calibre_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_lvs_run_file", sram22_lvs_recognize_gates_all
                )
            )
        if self.get_setting("technology.sky130.lvs_blackbox_srams"):
            calibre_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_lvs_run_file", calibre_lvs_blackbox_srams
                )
            )
            pegasus_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_lvs_ctl_file", pegasus_lvs_blackbox
                )
            )

        if self.get_setting("technology.sky130.stdcell_library") == "sky130_scl":
            pegasus_hooks.append(
                HammerTool.make_post_insertion_hook(
                    "generate_lvs_ctl_file", pegasus_lvs_add_130a_primitives
                )
            )
        hooks = {"calibre": calibre_hooks, "pegasus": pegasus_hooks}
        return hooks.get(tool_name, [])

# =============================================================================================

    # TODO(jimfang) these 2 are so hacked up...
    @staticmethod
    def sky130_sram_names() -> List[str]:
        sky130_sram_names = []
        sram_cache_json = importlib.resources.files("hammer.technology.sky130").joinpath("sram-cache.json").read_text()
        dl = json.loads(sram_cache_json)
        for d in dl:
            sky130_sram_names.append(d['name'])
        return sky130_sram_names

    @staticmethod
    def sky130_sram_primitive_names(
        sky130a_path: str, misc_tapeout_collateral_path: str
    ) -> List[str]:
        spice_filenames = [
            "sky130_fd_pr__pfet_01v8.pm3.spice",
            "sky130_fd_pr__nfet_01v8.pm3.spice",
            "sky130_fd_pr__pfet_01v8_hvt.pm3.spice",
            "sky130_fd_pr__special_nfet_latch.pm3.spice",
            "sky130_fd_pr__special_nfet_pass.pm3.spice",
            "sky130_fd_pr__nfet_01v8_lvt.pm3.spice",
            "sky130_fd_pr__nfet_g5v0d10v5.pm3.spice",
            "sky130_fd_pr__pfet_g5v0d10v5.pm3.spice",
            "sky130_fd_pr__nfet_05v0_nvt.pm3.spice",
            "sky130_fd_pr__esd_nfet_g5v0d10v5.pm3.spice",
            "sky130_fd_pr__cap_mim_m3_1.model.spice",
            "sky130_fd_pr__cap_mim_m3_2.model.spice",
        ]
        spice_dir = Path(sky130a_path) / "libs.ref" / "sky130_fd_pr" / "spice"
        pfet_latch_override = (
            Path(misc_tapeout_collateral_path)
            / "OPEN-PDKS-Modifications"
            / "sky130_fd_pr"
            / "sky130_fd_pr__special_pfet_latch.pm3.spice"
        ) if misc_tapeout_collateral_path else None
        paths = [
            str(
                pfet_latch_override
                if pfet_latch_override is not None and fname == pfet_latch_override.name
                else spice_dir / fname
            )
            for fname in spice_filenames
        ]
        return paths

    @staticmethod
    def sky130_por_primitive_names(
        sky130a_path: str, misc_tapeout_collateral_path: str
    ) -> List[str]:
        spice_dir = Path(sky130a_path) / "libs.ref" / "sky130_fd_pr" / "spice"
        spice_filenames = [
            "sky130_fd_pr__nfet_g5v0d10v5.pm3.spice",
            "sky130_fd_pr__pfet_g5v0d10v5.pm3.spice",
            "sky130_fd_pr__cap_mim_m3_1.model.spice",
            "sky130_fd_pr__cap_mim_m3_2.model.spice",
        ]
        override_paths = [] if not misc_tapeout_collateral_path else [
            Path(misc_tapeout_collateral_path)
            / "OPEN-PDKS-Modifications"
            / "sky130_fd_pr"
            / "sky130_fd_pr__special_pfet_pass.pm3.spice",
            Path(misc_tapeout_collateral_path)
            / "OPEN-PDKS-Modifications"
            / "sky130_fd_pr"
            / "sky130_fd_pr__res_xhigh_po.model.spice",
            Path(misc_tapeout_collateral_path)
            / "OPEN-PDKS-Modifications"
            / "sky130_fd_pr"
            / "sky130_fd_pr__model__parasitic__res_po.model.spice",
        ]
        paths = [str(spice_dir / fname) for fname in spice_filenames]
        paths.extend(str(path) for path in override_paths)
        paths.extend(
            [
                str(
                    Path(sky130a_path)
                    / "libs.tech"
                    / "ngspice"
                    / "sky130_fd_pr__model__r+c.model.spice"
                ),
                str(
                    Path(sky130a_path)
                    / "libs.ref"
                    / "sky130_fd_sc_hvl"
                    / "spice"
                    / "sky130_fd_sc_hvl.spice"
                ),
            ]
        )
        return paths

# =============================================================================================
# =============================================================================================

def _sky130_route_settings(ht: HammerTool) -> str:
    """Routing attributes. Innovus 23.1 renamed the route_design_* attributes to
    route_*; the older names are kept for the 21.1/22.1 environments."""
    antenna = ("sky130_fd_sc_hd__diode_2"
               if ht.get_setting("technology.sky130.stdcell_library") == "sky130_fd_sc_hd"
               else "ANTENNA")
    if ht.version() >= ht.version_number("231"):
        return f"""set_db route_antenna_diode_insertion true
set_db route_antenna_cell_name "{antenna}"

set_db route_high_freq_search_repair true
set_db route_detail_post_route_spread_wire true
set_db route_with_si_driven true
set_db route_with_timing_driven true
set_db route_concurrent_minimize_via_count_effort high
set_db opt_consider_routing_congestion true
set_db route_detail_use_multi_cut_via_effort high"""
    return f"""set_db route_design_antenna_diode_insertion 1
set_db route_design_antenna_cell_name "{antenna}"

set_db route_design_high_freq_search_repair true
set_db route_design_detail_post_route_spread_wire true
set_db route_design_with_si_driven true
set_db route_design_with_timing_driven true
set_db route_design_concurrent_minimize_via_count_effort high
set_db opt_consider_routing_congestion true
set_db route_design_detail_use_multi_cut_via_effort high"""


# various Innovus database settings
# Innovus Stylus Common UI Text Command Reference 23.17
def sky130_innovus_settings(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "Innovus settings only for par"
    assert isinstance(ht, TCLTool), "innovus settings can only run on TCL tools"
    """Settings for every tool invocation"""
    ht.append(
        f"""

##########################################################
# Placement attributes  [get_db -category place]
##########################################################
#-------------------------------------------------------------------------------
set_db place_global_place_io_pins true

set_db opt_honor_fences true
set_db place_detail_check_cut_spacing true
set_db place_global_cong_effort high
set_db place_detail_use_check_drc true
set_db place_detail_check_route true
set_db add_fillers_with_drc false

##########################################################
# Optimization attributes  [get_db -category opt]
##########################################################
#-------------------------------------------------------------------------------

set_db opt_fix_fanout_load true
set_db opt_area_recovery true
set_db opt_post_route_area_reclaim hold_and_setup_aware

##########################################################
# Clock attributes  [get_db -category cts]
##########################################################
#-------------------------------------------------------------------------------
set_db cts_target_skew 0.03
set_db cts_max_fanout 10
#set_db cts_target_max_transition_time .3
set_db opt_setup_target_slack 0.10
set_db opt_hold_target_slack 0.10

##########################################################
# Routing attributes  [get_db -category route]
##########################################################
#-------------------------------------------------------------------------------
{_sky130_route_settings(ht)}
    """
    )
    if ht.hierarchical_mode in {HierarchicalMode.Top, HierarchicalMode.Flat}:
        # Core and corner snapping are chip-level choices: the chipyard sky130
        # hammer-driver sets all three itself (set_die_snap_to_mfg_grid).
        ht.append(
            """
# For top module: snap die to manufacturing grid, not placement grid
set_db floorplan_snap_die_grid manufacturing
        """
        )

        # ht.append(
        #     """
    # # note this is required for sky130_fd_sc_hd, the design has a ton of drcs if bottom layer is 1
    # # TODO: why is setting routing_layer not enough?
    # set_db design_bottom_routing_layer 2
    # set_db design_top_routing_layer 6
    # # deprected syntax, but this used to always work
    # set_db route_design_bottom_routing_layer 2
    #           """
    # )

    return True

def sky130_connect_nets(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "connect global nets only for par"
    assert isinstance(ht, TCLTool), "connect global nets can only run on TCL tools"
    for pwr_gnd_net in (ht.get_all_power_nets() + ht.get_all_ground_nets()):
            if pwr_gnd_net.tie is not None:
                ht.append("connect_global_net {tie} -type pg_pin -pin_base_name {net} -all -auto_tie -netlist_override".format(tie=pwr_gnd_net.tie, net=pwr_gnd_net.name))
                ht.append("connect_global_net {tie} -type net    -net_base_name {net} -all -netlist_override".format(tie=pwr_gnd_net.tie, net=pwr_gnd_net.name))
    return True

# Pair VDD/VPWR and VSS/VGND nets
#   these commands are already added in Innovus.write_netlist,
#   but must also occur before power straps are placed
def sky130_connect_nets2(ht: HammerTool) -> bool:
    sky130_connect_nets(ht)
    return True


def power_rail_straps_no_tapcells(ht: HammerTool) -> bool:
    # only Hammer's generated by_tracks straps leave the M1 rails to this hook;
    # a manual or empty power-strap flow must not get a second set of rails
    if ht.get_setting("par.power_straps_mode") != "generate" or \
            ht.get_setting("par.generate_power_straps_method") != "by_tracks":
        return True
    assert not ht.get_setting(
        "par.generate_power_straps_options.by_tracks.generate_rail_layer"
    ), """
Rails must be placed by this hook for sky130_scl!
Set par.generate_power_straps_options.by_tracks.generate_rail_layer: false"""
    #  We do this since there are no explicit tapcells in sky130_scl
    # just need the rail ones, others are placed as usual.
    ht.append(
        """
# Power strap definition for layer met1 (rails):
# should be .14

reset_db -category add_stripes

# M1-only stripe generation. Do not generate vias during this add_stripes
# operation; M1<->M2 vias are added explicitly afterward.
set_db add_stripes_stacked_via_bottom_layer met1
set_db add_stripes_stacked_via_top_layer met1

# set_db add_stripes_spacing_from_block 4.0
# set_db add_stripes_spacing_from_block 0.0 

# set_db add_stripes_ignore_block_check false

# set_db add_stripes_ignore_drc true

set rail_width       0.400
set rail_spacing     3.740
set same_net_pitch   8.280

puts "  rail width:       $rail_width"
puts "  rail spacing:     $rail_spacing"
puts "  same-net pitch:   $same_net_pitch"
puts "  core bbox:        [get_db designs .core_bbox]"

add_stripes \
    -nets {VDD VSS} \
    -layer met1 \
    -direction horizontal \
    -start_from bottom \
    -start_offset \
    -0.200 \
    -width $rail_width \
    -spacing $rail_spacing \
    -set_to_set_distance $same_net_pitch \
    -switch_layer_over_obs false \
    -max_same_layer_jog_length 2.0 \
    -pad_core_ring_top_layer_limit met1 \
    -pad_core_ring_bottom_layer_limit met1 \
    -block_ring_top_layer_limit met1 \
    -block_ring_bottom_layer_limit met1 \
    -use_wire_group 0 \
    -snap_wire_center_to_grid none
    
reset_db -category add_stripes
"""
    )
    return True

def sky130_add_endcaps(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "endcap insertion only for par"
    assert isinstance(ht, TCLTool), "endcap insertion can only run on TCL tools"
    endcap_cells=ht.technology.get_special_cell_by_type(CellType.EndCap)
    endcap_cell=endcap_cells[0].name[0]
    ht.append(
        f'''
set_db add_endcaps_boundary_tap     true
set_db add_endcaps_left_edge        {endcap_cell}
set_db add_endcaps_right_edge       {endcap_cell}
add_endcaps
    '''
    )
    return True

def calibre_drc_blackbox_srams(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerDRCTool), "Exlude SRAMs only in DRC"
    drc_box = ''
    for name in SKY130Tech.sky130_sram_names():
        drc_box += f"\nEXCLUDE CELL {name}"
    run_file = ht.drc_run_file  # type: ignore
    with open(run_file, "a") as f:
        f.write(drc_box)
    return True


def false_rules_off(x: HammerTool) -> bool:
    # in sky130_rev_0.0_2.3 or newer decks, cadence included a .cfg to turn off false rules - this typically has to be loaded via the GUI but this step hacks the flags into pegasusdrcctl and turns the false rules off
    # if FALSEOFF is defined, the rules are turned off
    # docs for hooks: /scratch/ee198-20-aaf/barduino-ofot/vlsi/hammer/hammer/drc/pegasus/README.md

    # Following is for version 0.0_2.10 of DRC decks - configurator options in sky130_release_0.0.9/Sky130_DRC/sky130.drc.cfg
    # to update these, load the cfg file into the GUI, then look at the updated pegasusdrcctl file and see what the configurator sets variables to
    drc_box = """
//=== Configurator controls ===
#UNDEFINE FALSEOFF
//      (these rules are on by default and may produce false violations)
#UNDEFINE SRAM
// These are the affected rules and alternate implementation: 
//    licon.4a: licon in areaid.ce must overlap LI
//    licon.4b: licon in areaid.ce must overlap (poly or diff or tap)
//    mcon.cover.1: mcon in areaid.ce must overlap LI
// ** WARNING: This switch is for debugging purposes only, errors may be missed **
#UNDEFINE NODEN
//  Recommended Rules (RC,RR) & Guidelines (NC)
#UNDEFINE RC
#UNDEFINE NC
//  Optional Switches 
#UNDEFINE frontend
#UNDEFINE backend
//=== End of configurator controls ===
    """
    run_file = x.drc_ctl_file  # type: ignore
    with open(run_file, "a") as f:
        f.write(drc_box)
    return True


def pegasus_drc_blackbox_srams(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerDRCTool), "Exlude SRAMs only in DRC"
    drc_box = ''
    for name in SKY130Tech.sky130_sram_names():
        drc_box += f"\nexclude_cell {name}"
    run_file = ht.drc_ctl_file  # type: ignore
    with open(run_file, "a") as f:
        f.write(drc_box)
    return True

def calibre_lvs_blackbox_srams(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerLVSTool), "Blackbox and filter SRAMs only in LVS"
    lvs_box = ''
    for name in SKY130Tech.sky130_sram_names():
        lvs_box += f"\nLVS BOX {name}"
        lvs_box += f"\nLVS FILTER {name} OPEN "
    lvs_box += "\n"
    run_file = ht.lvs_run_file  # type: ignore
    with open(run_file, "a") as f:
        f.write(lvs_box)
    return True

def pegasus_lvs_blackbox_srams(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerLVSTool), "Blackbox and filter SRAMs only in LVS"
    lvs_box = ''
    for name in SKY130Tech.sky130_sram_names():
        lvs_box += f"\nlvs_black_box {name} -gray"
    run_file = ht.lvs_ctl_file  # type: ignore
    with open(run_file, "r+") as f:
        # Remove SRAM SPICE file includes.
        pattern = 'schematic_path.*({}).*spice;\n'.format('|'.join(SKY130Tech.sky130_sram_names()))
        matcher = re.compile(pattern)
        contents = f.read()
        fixed_contents = matcher.sub("", contents) + lvs_box
        f.seek(0)
        f.write(fixed_contents)
    return True

# we'll use 1 hook for all lvs backboxes
def pegasus_lvs_blackbox(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerLVSTool), "Blackbox and filter SRAMs only in LVS"
    lvs_box = ""
    for name in SKY130Tech.sky130_sram_names():
        lvs_box += f"\nlvs_black_box {name} -gray"
    run_file = ht.lvs_ctl_file  # type: ignore
    lvs_box += f"\nlvs_black_box simple_por -gray"
    lvs_box += f"\nlvs_black_box sky130_ef_io__* -black"
    lvs_box += f"\nlvs_black_box sky130_fd_io__* -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vssio_hvc_clamped_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vssa_hvc_clamped_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vssd_lvc_clamped3_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vdda_hvc_clamped_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vccd_lvc_clamped3_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vssd_lvc_clamped_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vccd_lvc_clamped_pad -black"
    lvs_box += f"\nlvs_black_box sky130_ef_io__vddio_hvc_clamped_pad -black"
    lvs_box += f"\nlvs_discard_pins yes"
    lvs_box += f"\nlvs_report_max -all"

    with open(run_file, "r+") as f:
        # Remove SRAM SPICE file includes and specific hardcoded sram22.spice include - this file is on bwrc servers, not on inst machines
        sram_names_regex = "|".join(SKY130Tech.sky130_sram_names())
        pattern = rf'schematic_path\s+.*({sram_names_regex}|sram22\.spice)".*spice;\n'
        matcher = re.compile(pattern)

        contents = f.read()
        fixed_contents = matcher.sub("", contents)

        # Replace lvs_power_name and lvs_ground_name lines
        fixed_contents = re.sub(
            r'lvs_power_name\s+VDD\s*;',
            'lvs_power_name VDD VPWR vdd VPB VPWR vdd1v8 VCCD VCCHIB VCCD1;',
            fixed_contents
        )
        fixed_contents = re.sub(
            r'lvs_ground_name\s+VSS\s*;',
            'lvs_ground_name VSS VGND vss VNB LVGND vss1v8 VGND vss3v3 VNB VSSIO VSSIO_Q VSSD VSSD1 VSSA;',
            fixed_contents
        )

        fixed_contents += lvs_box

        f.seek(0)
        f.write(fixed_contents)
        f.truncate()
    return True

# Required for SRAM22 and the PoR since they use sky130A primitives.
def pegasus_lvs_add_130a_primitives(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerLVSTool), "Add sky130A primitives only in LVS"
    sky130a_path = ht.get_setting("technology.sky130.sky130A")
    misc_tapeout_collateral_path = ht.get_setting(
        "technology.sky130.misc_tapeout_collateral"
    )
    # The PoR primitives are only needed for the tapeout collateral's PoR, and they include
    # the ngspice r+c model, which Pegasus cannot parse in older sky130A builds (BWRC's
    # 1.0.471 aborts LVS with NVN-15310), so add them only when that collateral is set.
    por_primitive_paths = SKY130Tech.sky130_por_primitive_names(
        sky130a_path, misc_tapeout_collateral_path
    ) if misc_tapeout_collateral_path else []
    primitive_paths = list(
        dict.fromkeys(
            SKY130Tech.sky130_sram_primitive_names(
                sky130a_path, misc_tapeout_collateral_path
            )
            + por_primitive_paths
        )
    )
    lvs_box = ""
    for name in primitive_paths:
        lvs_box += f"""\nschematic_path "{name}" spice;"""
    # this is because otherwise lvs crashes with tons of stdcell-level pin mismatches
    lvs_box += """\nlvs_inconsistent_reduction_threshold -none;"""
    run_file = ht.lvs_ctl_file  # type: ignore
    with open(run_file, "r+") as f:
        # Match primitive SPICE file includes already present in the control file.
        pattern = "schematic_path.*({}).*spice;\n".format(
            "|".join(primitive_paths)
        )
        matcher = re.compile(pattern)
        contents = f.read()
        fixed_contents = contents + lvs_box
        f.seek(0)
        f.write(fixed_contents)
    return True

def sram22_lvs_recognize_gates_all(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerLVSTool), "Change 'LVS RECOGNIZE GATES' from 'NONE' to 'ALL' for SRAM22"
    run_file = ht.lvs_run_file  # type: ignore
    with open(run_file, "a") as f:
        f.write("\nLVS RECOGNIZE GATES ALL\n")
    return True

# exclude sky130A IOs from pegasus for now
# in theory IO cells should be DRC clean or all DRCs should be waiveable.
def pegasus_drc_blackbox_io_cells(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerDRCTool) and ht.tool_config_prefix() == "drc.pegasus", (
        "Exlude IOs only for Pegasus DRC"
    )
    drc_box = ""
    io_cell_names = [
        "sky130_ef_io__*"
    ]  
    for name in io_cell_names:
        drc_box += f"\nexclude_cell {name}"
    run_file = ht.drc_ctl_file  # type: ignore
    with open(run_file, "a") as f:
        f.write(drc_box)
    return True

# we need this because:
# * (while cadence std cell ports are on M1), IO cells (openPDKs) ports are still on LI1
# * we need to tell Innovus to see/be aware of the LI1 layer (since ports are on that layer), as opposed to abstracting it out (and only seeing met1 & above).
# * not adding li1 as masterslice => openPDK io cell ports not being seen; if it is not set to a masterslice, Innovus will use LI1 as a routing layer, which will cause congestion + problems when other [cadence] cells' ports are on Met1.
# * nwell & pwell are copied from sky130_fd_sc_hd__nom.tlef with the same argument

# licon changes Needed in order to enforce the layer ordering in Innovus, otherwise may get error such as 
# You need to have cut layer after layer 'pwell'.
# broadly this is a repeat of information from the stackup image
_additional_tlef_edit_for_scl = """
LAYER nwell
  TYPE MASTERSLICE ;
END nwell
LAYER pwell
  TYPE MASTERSLICE ;
END pwell
LAYER li1
  TYPE MASTERSLICE ;
END li1
LAYER AREAIDLD
  TYPE MASTERSLICE ;
END AREAIDLD
LAYER licon
  TYPE CUT ;
END licon
"""

# sky130_fd_sc_hd tech-LEF edit, appended after END pwell
_the_tlef_edit = """
LAYER AREAIDLD
  TYPE MASTERSLICE ;
END AREAIDLD

LAYER licon
  TYPE CUT ;
END licon
"""

LVS_DECK_INSERT_LINES = """
LVS FILTER D  OPEN  SOURCE
LVS FILTER D  OPEN  LAYOUT
"""

LVS_DECK_SCRUB_LINES = [
    "VIRTUAL CONNECT REPORT",
    "SOURCE PRIMARY",
    "SOURCE SYSTEM SPICE",
    "SOURCE PATH",
    "ERC",
    "LVS REPORT",
]

def _cache_tmp_path(dest: Path) -> Path:
    return dest.with_name(f"{dest.name}.tmp.{os.getpid()}.{threading.get_ident()}")


def _write_cache_file(dest, text: str) -> None:
    """Publish a tech-cache file atomically. Parallel Hammer processes (DAG tasks
    sharing an obj_dir) load the technology into the same cache at the same time,
    and an in-place rewrite lets a sibling read a truncated file."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_tmp_path(dest)
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, dest)


def _copy_cache_file(src, dest) -> None:
    """Atomic counterpart of shutil.copy2 for the tech cache."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_tmp_path(dest)
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _missing_layer_blocks(edit: str, tlef_text: str) -> str:
    """The LAYER ... END blocks of edit that tlef_text does not already define,
    so appending the edit cannot duplicate a layer the PDK already has."""
    defined = set(re.findall(r"^\s*LAYER\s+(\S+)\s*$", tlef_text, re.M))
    blocks = re.findall(r"(LAYER\s+(\S+)\s*\n.*?\nEND\s+\2[ \t]*\n)", edit, re.S)
    if not any(name in defined for _, name in blocks):
        return edit
    return "".join("\n" + block for block, name in blocks if name not in defined)


tech = SKY130Tech()
#  hammer-vlsi plugin for Cadence Innovus.
#
#  See LICENSE for licence details.

import shutil
from typing import List, Dict, Optional, Callable, Tuple, Any, cast
from itertools import chain

import os
import errno
import re

from hammer.utils import get_or_else, optional_map
from hammer.vlsi import HammerTool, HammerPlaceAndRouteTool, HammerToolStep, HammerToolHookAction, \
    PlacementConstraintType, HierarchicalMode, ILMStruct, ObstructionType, Margins, Supply, PlacementConstraint, MMMCCornerType
from hammer.vlsi.units import CapacitanceValue
from hammer.logging import HammerVLSILogging
import hammer.tech as hammer_tech
from hammer.tech import RoutingDirection
from hammer.tech.specialcells import CellType
from decimal import Decimal
from hammer.common.cadence import CadenceTool

# Notes: camelCase commands are the old syntax (deprecated)
# snake_case commands are the new/common UI syntax.
# This plugin should only use snake_case commands.

class InnovusTclToPythonConverter:
    """Converts Innovus TCL scripts to Python following Innovus 25.1 Python API rules"""
    
    # Python reserved keywords that need _option_ prefix
    RESERVED_KEYWORDS = {
        'False', 'break', 'for', 'not', 'None', 'class', 'from', 'or', 
        'True', 'continue', 'global', 'pass', 'def', 'if', 'raise', 
        'and', 'del', 'import', 'return', 'as', 'elif', 'in', 'try', 
        'assert', 'else', 'is', 'while', 'async', 'except', 'lambda', 
        'with', 'await', 'finally', 'nonlocal', 'yield'
    }
    
    # Commands that should use tcl.eval() to avoid issues
    FALLBACK_TO_TCL = {
        'write_db',  # Can segfault in Python
        'ln',        # Shell command
        'source',    # Source TCL files
        'exit'       # Exit command
    }
    
    # Commands where output file is positional (not named)
    POSITIONAL_OUTPUT_CMDS = {
        'write_stream': True,
        'write_sdf': True,
        'write_def': True,
        'write_netlist': True,
        'write_parasitics': True
    }
    
    def __init__(self):
        self.output_lines = []

    def _is_register_file_io_block(self, lines: List[str], start_idx: int) -> Tuple[bool, int]:
        """
        Detect register file I/O blocks - these must be wrapped entirely in tcl.eval()
        
        Pattern to match:
            set VAR "./find_regs_*.json"
            set VAR [open $VAR "w"]
            puts $VAR ...
            ... (complex TCL code including for-loops)
            close $VAR
        """
        if start_idx >= len(lines):
            return False, start_idx
        
        line = lines[start_idx].strip()
        
        # Must start with 'set ' (after stripping whitespace)
        if not line.startswith('set '):
            return False, start_idx
        
        # Must contain .json
        if '.json' not in line:
            return False, start_idx
        
        # Must be a find_regs file (cells, paths, or regs)
        if 'find_regs' not in line:
            return False, start_idx
        
        # Extract variable name
        parts = line.split()
        if len(parts) < 3:
            return False, start_idx
        
        var_name = parts[1]
        
        # Search for matching close statement
        # Increased range to 30 lines to handle larger blocks
        end_idx = start_idx + 1
        found_close = False
        
        while end_idx < len(lines) and (end_idx - start_idx) < 30:
            current = lines[end_idx].strip()
            
            # Look for: close $VAR or close VAR
            if current.startswith('close '):
                # Check if variable name appears in close statement
                if var_name in current or f'${var_name}' in current:
                    found_close = True
                    break
            
            end_idx += 1
        
        # DEBUG: Uncomment to trace detection
        # if found_close:
        #     print(f"DEBUG: Detected file I/O block: '{var_name}' from line {start_idx+1} to {end_idx+1}")
        
        return found_close, end_idx if found_close else start_idx


    def _wrap_block_in_tcl_eval(self, lines: List[str], start_idx: int, end_idx: int) -> str:
        """Wrap TCL block in tcl.eval() with proper backslash handling"""
        block_lines = []
        
        for i in range(start_idx, end_idx + 1):
            line = lines[i].rstrip()
            if line:
                block_lines.append(line)
        
        tcl_block = '\n'.join(block_lines)
        
        # Use raw string (r'''...''') to preserve backslashes!
        return f"tcl.eval(r'''{tcl_block}''')"  # ← Note the 'r' prefix!

        
    def convert_tcl_to_python(self, tcl_content: str) -> str:
        """Convert TCL content to Python string"""
        self.output_lines = []
        
        # Add header
        self.output_lines.append("# " + "=" * 78)
        self.output_lines.append("# Auto-converted from TCL to Python for Innovus 25.1")
        self.output_lines.append("# " + "=" * 78)
        self.output_lines.append("")
        
        tcl_lines = tcl_content.split('\n')
        
        i = 0
        while i < len(tcl_lines):
            line = tcl_lines[i].rstrip()
            
            # Check if this is the start of a register file I/O block
            is_file_io, end_idx = self._is_register_file_io_block(tcl_lines, i)
            
            if is_file_io:
                # Wrap the entire block in tcl.eval()
                self.output_lines.append("# Complex file I/O block - using tcl.eval()")
                tcl_eval_line = self._wrap_block_in_tcl_eval(tcl_lines, i, end_idx)
                self.output_lines.append(tcl_eval_line)
                self.output_lines.append("")
                i = end_idx + 1
                continue
            
            # Handle multi-line commands
            full_line = line
            while i + 1 < len(tcl_lines) and (line.endswith('\\\\') or self._has_unclosed_braces(full_line)):
                i += 1
                line = tcl_lines[i].rstrip()
                full_line += ' ' + line.lstrip()
            
            converted = self.convert_line(full_line)
            if converted:
                self.output_lines.append(converted)
            
            i += 1
        
        return '\n'.join(self.output_lines)
    
    def _has_unclosed_braces(self, line: str) -> bool:
        """Check if line has unclosed braces"""
        open_count = line.count('{')
        close_count = line.count('}')
        return open_count > close_count
    
    def convert_line(self, line: str) -> str:
        """Convert a single TCL line to Python"""
        line = line.strip()
        
        # Skip empty lines and pure comments
        if not line or line.startswith('#'):
            if line.startswith('#'):
                return line
            return ''
        
        # Handle 'puts' statements
        if line.startswith('puts '):
            return self._convert_puts(line)
        
        # Handle 'close' command
        if line.startswith('close '):
            var_name = line.split()[1].strip('$')
            return f"{var_name}.close()"
        
        # Handle 'open' command
        if 'open ' in line and ('[open' in line or line.startswith('set ')):
            return self._convert_file_open(line)
        
        # Check if this is a command we should fallback to tcl.eval()
        cmd_name = self._extract_command_name(line)
        if cmd_name in self.FALLBACK_TO_TCL or cmd_name.startswith('ln '):
            escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
            return f"tcl.eval('{escaped_line}')"
        
        # Handle set_db commands
        if line.startswith('set_db '):
            return self._convert_set_db(line)
        
        # Handle get_db -> db() conversions
        if 'get_db' in line:
            return self._convert_get_db_line(line)
        
        # Handle regular commands
        if self._is_command(line):
            return self._convert_command(line)
        
        # Handle TCL control structures
        if line.startswith('if {') or line.startswith('for {') or line.startswith('while {'):
            escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
            return f"tcl.eval('{escaped_line}')"
        
        # Handle variable assignments
        if line.startswith('set '):
            return self._convert_variable_assignment(line)
        
        # Default fallback: wrap in tcl.eval()
        escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
        return f"tcl.eval('{escaped_line}')"
    
    def _convert_puts(self, line: str) -> str:
        """Convert TCL puts to Python print"""
        if line.startswith('puts "'):
            content = line[6:-1]
            content = content.replace('"', '\\"')
            return f'print("{content}")'
        
        match = re.match(r'puts\s+(.+)', line)
        if match:
            var_expr = match.group(1).strip()
            
            # Handle file handle: puts $file_handle "content"
            if ' "' in var_expr or " '" in var_expr:
                parts = var_expr.split(maxsplit=1)
                file_var = parts[0].strip('$')
                content = parts[1].strip('"\'')
                content = content.replace('"', '\\"')
                return f'{file_var}.write("{content}\\n")'
            
            if var_expr.startswith('$'):
                var_name = var_expr[1:]
                return f'print({var_name})'
            
            return f'print({var_expr})'
        
        return f"print({line[5:]})"
    
    def _convert_file_open(self, line: str) -> str:
        """Convert TCL file open to Python open()"""
        match = re.match(r'set\s+(\w+)\s+\[open\s+"([^"]+)"\s+"([^"]+)"\]', line)
        if match:
            var_name = match.group(1)
            filename = match.group(2)
            mode = match.group(3)
            return f'{var_name} = open("{filename}", "{mode}")'
        
        match = re.match(r'set\s+(\w+)\s+\[open\s+\$(\w+)\s+"([^"]+)"\]', line)
        if match:
            var_name = match.group(1)
            filename_var = match.group(2)
            mode = match.group(3)
            return f'{var_name} = open({filename_var}, "{mode}")'
        
        escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
        return f"tcl.eval('{escaped_line}')"
    
    def _extract_command_name(self, line: str) -> str:
        """Extract the command name from a line"""
        parts = line.split()
        if parts:
            return parts[0]
        return ''
    
    def _is_command(self, line: str) -> bool:
        """Check if line is a Innovus command"""
        tcl_keywords = {'if', 'for', 'while', 'foreach', 'proc', 'set'}
        cmd = self._extract_command_name(line)
        return cmd and cmd not in tcl_keywords
    
    def _convert_set_db(self, line: str) -> str:
        """Convert set_db commands to Python db() attribute access"""
        match = re.match(r'set_db\s+(\S+)\s+(.+)', line)
        if match:
            attr_name = match.group(1)
            value = match.group(2).strip()
            py_value = self._convert_value(value)
            return f"db().{attr_name} = {py_value}"
        
        return f"# FIXME: Could not convert: {line}"
    
    def _convert_get_db_line(self, line: str) -> str:
        """Convert lines containing get_db to use db() API"""
        # Pattern: set var [get_db objects pattern]
        match = re.match(r'set\s+(\w+)\s+\[get_db\s+(\w+)\s+([^\]]+)\]', line)
        if match:
            var_name = match.group(1)
            obj_type = match.group(2)
            pattern = match.group(3).strip()
            
            if '-if' in pattern:
                escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
                return f"{var_name} = tcl.eval('{escaped_line}').split()"
            
            if pattern.startswith('{') and pattern.endswith('}'):
                pattern = pattern[1:-1]
            
            pattern = pattern.strip('"\'')
            return f"{var_name} = db().{obj_type}('{pattern}')"
        
        if '[get_db' in line:
            escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
            return f"tcl.eval('{escaped_line}')"
        
        return line
    
    def _convert_command(self, line: str) -> str:
        """Convert a TCL command to Python function call"""
        parts = self._tokenize_command(line)
        if not parts:
            return f"# FIXME: Empty command"
        
        cmd_name = parts[0]
        positional_args = []
        named_args = {}
        
        i = 1
        while i < len(parts):
            part = parts[i]
            
            if part.startswith('-'):
                flag_name = part[1:]
                
                if i + 1 >= len(parts) or parts[i + 1].startswith('-'):
                    named_args[flag_name] = True
                    i += 1
                else:
                    flag_value = parts[i + 1]
                    
                    if flag_name in self.RESERVED_KEYWORDS:
                        flag_name = f'_option_{flag_name}'
                    
                    named_args[flag_name] = self._convert_value(flag_value)
                    i += 2
            else:
                positional_args.append(self._convert_value(part))
                i += 1
        
        # Special handling for commands with positional output files
        if cmd_name in self.POSITIONAL_OUTPUT_CMDS:
            if 'output' in named_args:
                output_val = named_args.pop('output')
                positional_args.insert(0, output_val)
        
        # Build Python function call
        py_args = []
        py_args.extend(positional_args)
        
        for key, val in named_args.items():
            if isinstance(val, bool) and val:
                py_args.append(f"{key}=True")
            elif isinstance(val, bool) and not val:
                py_args.append(f"{key}=False")
            else:
                py_args.append(f"{key}={val}")
        
        args_str = ', '.join(py_args)
        return f"{cmd_name}({args_str})"
    
    def _tokenize_command(self, line: str) -> List[str]:
        """Tokenize a TCL command into parts, respecting braces and quotes"""
        tokens = []
        current_token = ''
        in_braces = 0
        in_quotes = False
        escape_next = False
        
        for char in line:
            if escape_next:
                current_token += char
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                continue
            
            if char == '"' and in_braces == 0:
                in_quotes = not in_quotes
                current_token += char
                continue
            
            if char == '{' and not in_quotes:
                in_braces += 1
                current_token += char
                continue
            
            if char == '}' and not in_quotes:
                in_braces -= 1
                current_token += char
                if in_braces == 0 and current_token:
                    tokens.append(current_token)
                    current_token = ''
                continue
            
            if char in ' \t' and in_braces == 0 and not in_quotes:
                if current_token:
                    tokens.append(current_token)
                    current_token = ''
                continue
            
            current_token += char
        
        if current_token:
            tokens.append(current_token)
        
        return tokens
    
    def _convert_value(self, value: str) -> str:
        """Convert a TCL value to Python representation"""
        value = value.strip()
        
        # Handle braced lists
        if value.startswith('{') and value.endswith('}'):
            inner = value[1:-1].strip()
            items = self._tokenize_list(inner)
            
            if len(items) == 1:
                return self._convert_value(items[0])
            
            converted_items = [self._convert_value(item) for item in items]
            return f"[{', '.join(converted_items)}]"
        
        # Handle quoted strings
        if value.startswith('"') and value.endswith('"'):
            inner = value[1:-1]
            inner = inner.replace("'", "\\'")
            return f"'{inner}'"
        
        # Handle numbers
        if value.replace('.', '').replace('-', '').replace('+', '').isdigit():
            return value
        
        # Handle booleans
        if value.lower() == 'true':
            return 'True'
        if value.lower() == 'false':
            return 'False'
        
        # Handle TCL variables
        if value.startswith('$'):
            return f"'{value}'"
        
        # Default: treat as string
        return f"'{value}'"
    
    def _tokenize_list(self, content: str) -> List[str]:
        """Tokenize a TCL list, preserving complete items even with spaces"""
        tokens = []
        current_token = ''
        in_braces = 0
        in_quotes = False
        escape_next = False
        
        for char in content:
            if escape_next:
                current_token += char
                escape_next = False
                continue
            
            if char == '\\':
                escape_next = True
                current_token += char
                continue
            
            if char == '"' and in_braces == 0:
                in_quotes = not in_quotes
                current_token += char
                continue
            
            if char == '{' and not in_quotes:
                in_braces += 1
                current_token += char
                continue
            
            if char == '}' and not in_quotes:
                in_braces -= 1
                current_token += char
                continue
            
            if char in ' \t\n' and in_braces == 0 and not in_quotes:
                if current_token:
                    tokens.append(current_token)
                    current_token = ''
                continue
            
            current_token += char
        
        if current_token:
            tokens.append(current_token)
        
        return tokens
    
    def _convert_variable_assignment(self, line: str) -> str:
        """Convert TCL variable assignment to Python"""
        match = re.match(r'set\s+(\w+)\s+(.+)', line)
        if match:
            var_name = match.group(1)
            value = match.group(2).strip()
            
            if '[' in value:
                escaped_line = line.replace('\\', '\\\\').replace("'", "\\'")
                return f"{var_name} = tcl.eval('{escaped_line}')"
            
            py_value = self._convert_value(value)
            return f"{var_name} = {py_value}"
        
        return f"# FIXME: Could not convert variable: {line}"

class Innovus(HammerPlaceAndRouteTool, CadenceTool):

    def export_config_outputs(self) -> Dict[str, Any]:
        outputs = dict(super().export_config_outputs())
        # TODO(edwardw): find a "safer" way of passing around these settings keys.
        outputs["par.outputs.seq_cells"] = self.output_seq_cells
        outputs["par.outputs.all_regs"] = self.output_all_regs
        outputs["par.outputs.sdf_file"] = self.output_sdf_path
        outputs["par.outputs.def_file"] = self.output_def_path
        outputs["par.outputs.spefs"] = self.output_spef_paths
        return outputs

    def fill_outputs(self) -> bool:
        if self.ran_write_ilm:
            # Check that the ILMs got written.

            ilm_data_dir = "{ilm_dir_name}/mmmc/ilm_data/{top}".format(ilm_dir_name=self.ilm_dir_name,
                                                                       top=self.top_module)
            postRoute_v_gz = os.path.join(ilm_data_dir, "{top}_postRoute.v.gz".format(top=self.top_module))

            if not os.path.isfile(postRoute_v_gz):
                raise ValueError("ILM output postRoute.v.gz %s not found" % (postRoute_v_gz))

            # Copy postRoute.v.gz to postRoute.ilm.v.gz since that's what Genus seems to expect.
            postRoute_ilm_v_gz = os.path.join(ilm_data_dir, "{top}_postRoute.ilm.v.gz".format(top=self.top_module))
            shutil.copyfile(postRoute_v_gz, postRoute_ilm_v_gz)

            # Write output_ilms.
            self.output_ilms = [
                ILMStruct(dir=self.ilm_dir_name, data_dir=ilm_data_dir, module=self.top_module,
                          lef=os.path.join(self.run_dir, "{top}ILM.lef".format(top=self.top_module)),
                          gds=self.output_gds_filename,
                          netlist=self.output_netlist_filename,
                          sim_netlist=self.output_sim_netlist_filename,
                          sdcs=self.output_ilm_sdcs)
            ]
        else:
            self.output_ilms = []

        # Check that the regs paths were written properly if the write_regs step was run
        self.output_seq_cells = self.all_cells_path
        self.output_all_regs = self.all_regs_path
        if self.ran_write_regs:
            if not os.path.isfile(self.all_cells_path):
                raise ValueError("Output find_regs_cells.json %s not found" % (self.all_cells_path))

            if not os.path.isfile(self.all_regs_path):
                raise ValueError("Output find_regs_paths.json %s not found" % (self.all_regs_path))

            if not self.process_reg_paths(self.all_regs_path):
                self.logger.error("Failed to process all register paths")
        else:
            self.logger.info("Did not run write_regs")

        # Check that the par outputs exist if the par run was successful
        self.output_gds = self.output_gds_filename
        self.output_netlist = self.output_netlist_filename
        self.output_sim_netlist = self.output_sim_netlist_filename
        self.hcells_list = []
        self.sdf_file = self.output_sdf_path
        self.spef_files = self.output_spef_paths

        if self.ran_write_design:
            if not os.path.isfile(self.output_gds_filename):
                raise ValueError("Output GDS %s not found" % (self.output_gds_filename))

            if not os.path.isfile(self.output_netlist_filename):
                raise ValueError("Output netlist %s not found" % (self.output_netlist_filename))

            if not os.path.isfile(self.output_sim_netlist_filename):
                raise ValueError("Output sim netlist %s not found" % (self.output_sim_netlist_filename))

            if not os.path.isfile(self.output_sdf_path):
                raise ValueError("Output SDF %s not found" % (self.output_sdf_path))

            for spef_path in self.output_spef_paths:
                if not os.path.isfile(spef_path):
                    raise ValueError("Output SPEF %s not found" % (spef_path))
        else:
            self.logger.info("Did not run write_design")

        return True

    @property
    def output_gds_filename(self) -> str:
        return os.path.join(self.run_dir, "{top}.gds".format(top=self.top_module))

    @property
    def output_netlist_filename(self) -> str:
        return os.path.join(self.run_dir, "{top}.lvs.v".format(top=self.top_module))

    @property
    def output_physical_netlist_filename(self) -> str:
        return os.path.join(self.run_dir, "{top}.physical.v".format(top=self.top_module))

    @property
    def output_sim_netlist_filename(self) -> str:
        return os.path.join(self.run_dir, "{top}.sim.v".format(top=self.top_module))

    @property
    def all_regs_path(self) -> str:
        return os.path.join(self.run_dir, "find_regs_paths.json")

    @property
    def all_cells_path(self) -> str:
        return os.path.join(self.run_dir, "find_regs_cells.json")

    @property
    def output_sdf_path(self) -> str:
        return os.path.join(self.run_dir, "{top}.par.sdf".format(top=self.top_module))

    @property
    def output_def_path(self) -> str:
        return os.path.join(self.run_dir, "{top}.def".format(top=self.top_module))

    @property
    def output_spef_paths(self) -> List[str]:
        corners = self.get_mmmc_corners()
        if corners:
            # Order matters in tool consuming spefs (ensured here by get_mmmc_corners())!
            return list(map(lambda c: os.path.join(self.run_dir, "{top}.{corner}.par.spef".format(top=self.top_module, corner=c.name)), corners))
        else:
            return [os.path.join(self.run_dir, "{top}.par.spef".format(top=self.top_module))]

    @property
    def output_ilm_sdcs(self) -> List[str]:
        corners = self.get_mmmc_corners()
        if corners:
            filtered = list(filter(lambda c: c.type in [MMMCCornerType.Setup, MMMCCornerType.Hold], corners))
            ctype_map = {MMMCCornerType.Setup: "setup", MMMCCornerType.Hold: "hold"}
            return list(map(lambda c: os.path.join(self.run_dir, "{top}_postRoute_{corner_name}.{corner_type}_view.core.sdc".format(
                top=self.top_module, corner_name=c.name, corner_type=ctype_map[c.type])), filtered))
        else:
            return [os.path.join(self.run_dir, "{top}_postRoute.core.sdc".format(top=self.top_module))]

    @property
    def env_vars(self) -> Dict[str, str]:
        v = dict(super().env_vars)
        v["INNOVUS_BIN"] = self.get_setting("par.innovus.innovus_bin")
        if self.version() >= self.version_number("221"):  # support critical region resynthesis with DDI
            v["PATH"] = f'{os.environ.copy()["PATH"]}:{os.path.dirname(self.get_setting("par.innovus.innovus_bin").replace("INNOVUS", "GENUS"))}'
        if self.get_setting("par.innovus.signoff"):  # add path to Tempus
            v["PATH"] = v["PATH"] + f':{self.get_setting("cadence.cadence_home")}/SSV/SSV{self.get_setting("par.innovus.version")}/bin'
        return v

    @property
    def _step_transitions(self) -> List[Tuple[str, str]]:
        """
        Private helper property to keep track of which steps we ran so that we
        can create symlinks.
        This is a list of (pre, post) steps
        """
        return self.attr_getter("__step_transitions", [])

    @_step_transitions.setter
    def _step_transitions(self, value: List[Tuple[str, str]]) -> None:
        self.attr_setter("__step_transitions", value)

    def do_pre_steps(self, first_step: HammerToolStep) -> bool:
        assert super().do_pre_steps(first_step)
        # Restore from the last checkpoint if we're not starting over.
        if first_step != self.first_step:
            self.verbose_append("read_db pre_{step}".format(step=first_step.name))
        return True

    def do_between_steps(self, prev: HammerToolStep, next: HammerToolStep) -> bool:
        assert super().do_between_steps(prev, next)
        # Write a checkpoint to disk.
        self.verbose_append("write_db pre_{step}".format(step=next.name))
        # Symlink the database to latest for open_chip script later.
        self.verbose_append("ln -sfn pre_{step} latest".format(step=next.name))
        self._step_transitions = self._step_transitions + [(prev.name, next.name)]
        return True

    def do_post_steps(self) -> bool:
        assert super().do_post_steps()
        # Create symlinks for post_<step> to pre_<step+1> to improve usability.
        try:
            for prev, next in self._step_transitions:
                os.symlink(
                    os.path.join(self.run_dir, "pre_{next}".format(next=next)), # src
                    os.path.join(self.run_dir, "post_{prev}".format(prev=prev)) # dst
                )
        except OSError as e:
            if e.errno != errno.EEXIST:
                self.logger.warning("Failed to create post_* symlinks: " + str(e))

        # Create db post_<last step>
        # TODO: this doesn't work if you're only running the very last step
        if len(self._step_transitions) > 0:
            last = "post_{step}".format(step=self._step_transitions[-1][1])
            self.verbose_append("write_db {last}".format(last=last))
            # Symlink the database to latest for open_chip script later.
            self.verbose_append("ln -sfn {last} latest".format(last=last))

        return self.run_innovus()

    def get_tool_hooks(self) -> List[HammerToolHookAction]:
        return [self.make_persistent_hook(innovus_global_settings)]

    @property
    def steps(self) -> List[HammerToolStep]:
        steps = [
            self.init_design,
            self.floorplan_design,
            self.place_bumps,
            self.place_tap_cells,
            self.power_straps,
            self.place_pins,
            self.place_opt_design,
            self.clock_tree,
            self.add_fillers
        ]
        if self.version() >= self.version_number("231"):
            # Route & post-route opt are now combined
            steps += [self.route_opt_design]
        else:
            steps += [
                self.route_design,
                self.opt_design
            ]
        if self.get_setting("par.innovus.signoff"):
            steps += [self.opt_signoff]
        write_design_step = [
            self.write_regs,
            self.write_design
        ]  # type: List[Callable[[], bool]]
        if self.hierarchical_mode == HierarchicalMode.Flat:
            # Nothing to do
            pass
        elif self.hierarchical_mode == HierarchicalMode.Leaf:
            # All modules in hierarchical must write an ILM
            write_design_step += [self.write_ilm]
        elif self.hierarchical_mode == HierarchicalMode.Hierarchical:
            # All modules in hierarchical must write an ILM
            write_design_step += [self.write_ilm]
        elif self.hierarchical_mode == HierarchicalMode.Top:
            # No need to write ILM at the top.
            # Top needs assemble_design instead.
            steps += [self.assemble_design]
            pass
        else:
            raise NotImplementedError("HierarchicalMode not implemented: " + str(self.hierarchical_mode))
        return self.make_steps_from_methods(steps + write_design_step)

    def tool_config_prefix(self) -> str:
        return "par.innovus"

    def init_design(self) -> bool:
        """Initialize the design."""
        verbose_append = self.verbose_append

        # Perform common path pessimism removal in setup and hold mode
        verbose_append("set_db timing_analysis_cppr both")
        # Use OCV mode for timing analysis by default
        verbose_append("set_db timing_analysis_type ocv")

        # Read LEF layouts.
        lef_files = self.technology.read_libs([
            hammer_tech.filters.lef_filter
        ], hammer_tech.HammerTechnologyUtils.to_plain_item)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            ilm_lefs = list(map(lambda ilm: ilm.lef, self.get_input_ilms()))
            lef_files.extend(ilm_lefs)
        verbose_append("read_physical -lef {{ {files} }}".format(
            files=" ".join(lef_files)
        ))

        # Read timing libraries.
        mmmc_path = os.path.join(self.run_dir, "mmmc.tcl")
        self.write_contents_to_path(self.generate_mmmc_script(), mmmc_path)
        verbose_append("read_mmmc {mmmc_path}".format(mmmc_path=mmmc_path))

        # Read netlist.
        # Innovus only supports structural Verilog for the netlist; the Verilog can be optionally compressed.
        if not self.check_input_files([".v", ".v.gz"]):
            return False

        # We are switching working directories and we still need to find paths.
        abspath_input_files = list(map(lambda name: os.path.join(os.getcwd(), name), self.input_files))
        verbose_append("read_netlist {{ {files} }} -top {top}".format(
            files=" ".join(abspath_input_files),
            top=self.top_module
        ))

        if self.hierarchical_mode.is_nonleaf_hierarchical():
            # Read ILMs.
            for ilm in self.get_input_ilms():
                # Assumes that the ILM was created by Innovus (or at least the file/folder structure).
                verbose_append("read_ilm -cell {module} -directory {dir}".format(dir=ilm.dir, module=ilm.module))

        # Emit init_power_nets and init_ground_nets in case CPF/UPF is not used
        # commit_power_intent does not override power nets defined in "init_power_nets"
        spec_mode = self.get_setting("vlsi.inputs.power_spec_mode")  # type: str
        if spec_mode == "empty":
            power_supplies = self.get_independent_power_nets()  # type: List[Supply]
            power_nets = " ".join(map(lambda s: s.name, power_supplies))
            ground_supplies = self.get_independent_ground_nets()  # type: List[Supply]
            ground_nets = " ".join(map(lambda s: s.name, ground_supplies))
            verbose_append("set_db init_power_nets {{{n}}}".format(n=power_nets))
            verbose_append("set_db init_ground_nets {{{n}}}".format(n=ground_nets))

        # Run init_design to validate data and start the Cadence place-and-route workflow.
        verbose_append("init_design")

        # Set the top and bottom global/detail routing layers.
        # This must happen after the tech LEF is loaded
        layers = self.get_setting("vlsi.technology.routing_layers")
        if layers is not None:
            if self.version() >= self.version_number("201"):
                verbose_append(f"set_db design_bottom_routing_layer {layers[0]}")
                verbose_append(f"set_db design_top_routing_layer {layers[1]}")
            else:
                verbose_append(f"set_db route_early_global_bottom_layer {layers[0]}")
                verbose_append(f"set_db route_early_global_top_layer {layers[1]}")
                verbose_append(f"set_db route_design_bottom_layer {layers[0]}")
                verbose_append(f"set_db route_design_top_layer {layers[1]}")

        # Set design effort.
        verbose_append(f"set_db design_flow_effort {self.get_setting('par.innovus.design_flow_effort')}")
        verbose_append(f"set_db design_power_effort {self.get_setting('par.innovus.design_power_effort')}")

        return True

    def floorplan_design(self) -> bool:
        floorplan_tcl = os.path.join(self.run_dir, "floorplan.tcl")
        self.write_contents_to_path("\n".join(self.create_floorplan_tcl()), floorplan_tcl)
        self.verbose_append("source -echo -verbose {}".format(floorplan_tcl))

        # Set "don't use" cells.
        # This must happen after floorplan_design because it must run in flattened-mode
        # (after ILMs are placed)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("flatten_ilm")

        # Setup power settings from cpf/upf
        for l in self.generate_power_spec_commands():
            self.verbose_append(l)

        for l in self.generate_dont_use_commands():
            self.append(l)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("unflatten_ilm")

        return True

    def place_bumps(self) -> bool:
        bumps = self.get_bumps()
        if bumps is not None:
            bump_array_width = Decimal(str((bumps.x - 1) * bumps.pitch_x))
            bump_array_height = Decimal(str((bumps.y - 1) * bumps.pitch_y))
            fp_consts = self.get_placement_constraints()
            fp_width = Decimal(0)
            fp_height = Decimal(0)
            for const in fp_consts:
                if const.type == PlacementConstraintType.TopLevel:
                    fp_width = const.width
                    fp_height = const.height
            if fp_width == 0 or fp_height == 0:
                raise ValueError("Floorplan does not specify a TopLevel constraint or it has no dimensions")
            # Center bump array in the middle of floorplan
            bump_offset_x = (Decimal(str(fp_width)) - bump_array_width) / 2 + bumps.global_x_offset
            bump_offset_y = (Decimal(str(fp_height)) - bump_array_height) / 2+ bumps.global_y_offset
            power_ground_nets = list(map(lambda x: x.name, self.get_independent_power_nets() + self.get_independent_ground_nets()))
            # TODO: Fix this once the stackup supports vias ucb-bar/hammer#354
            block_layer = self.get_setting("vlsi.technology.bump_block_cut_layer")  # type: str
            for bump in bumps.assignments:
                self.append("create_bump -allow_overlap_control keep_all -cell {cell} -location_type cell_center -name_format \"Bump_{c}.{r}\" -orient r0 -location \"{x} {y}\"".format(
                    cell = bump.custom_cell if bump.custom_cell is not None else bumps.cell,
                    c = bump.x,
                    r = bump.y,
                    x = bump_offset_x + Decimal(str(bump.x - 1)) * Decimal(str(bumps.pitch_x)),
                    y = bump_offset_y + Decimal(str(bump.y - 1)) * Decimal(str(bumps.pitch_y))))
                if not bump.no_connect:
                    if bump.name in power_ground_nets:
                        self.append("select_bumps -bumps \"Bump_{x}.{y}\"".format(x=bump.x, y=bump.y))
                        self.append("assign_pg_bumps -selected -nets {n}".format(n=bump.name))
                        self.append("deselect_bumps")
                    else:
                        self.append("assign_signal_to_bump -bumps \"Bump_{x}.{y}\" -net {n}".format(x=bump.x, y=bump.y, n=bump.name))
                self.append("create_route_blockage -name Bump_{x}_{y}_blockage {layer_options} \"{llx} {lly} {urx} {ury}\"".format(
                    x = bump.x,
                    y = bump.y,
                    layer_options="-layers {{{l}}} -rects".format(l=block_layer) if(self.version() >= self.version_number("181")) else "-cut_layers {{{l}}} -area".format(l=block_layer),
                    llx = "[get_db bump:Bump_{x}.{y} .bbox.ll.x]".format(x=bump.x, y=bump.y),
                    lly = "[get_db bump:Bump_{x}.{y} .bbox.ll.y]".format(x=bump.x, y=bump.y),
                    urx = "[get_db bump:Bump_{x}.{y} .bbox.ur.x]".format(x=bump.x, y=bump.y),
                    ury = "[get_db bump:Bump_{x}.{y} .bbox.ur.y]".format(x=bump.x, y=bump.y)))

            # Use early global bump router
            if self.version() >= self.version_number("231") and self.get_setting("par.innovus.early_route_bumps"):
                self.append("set_db route_early_global_route_bump_nets true")
        return True

    def place_tap_cells(self) -> bool:
        tap_cells = self.technology.get_special_cell_by_type(CellType.TapCell)

        if len(tap_cells) == 0:
            self.logger.warning("Tap cells are improperly defined in the tech plugin and will not be added. This step should be overridden with a user hook or tapcell special cell should be added to the tech.json.")
            return True

        tap_cell = tap_cells[0].name[0]

        try:
            interval = self.get_setting("vlsi.technology.tap_cell_interval")
            offset = self.get_setting("vlsi.technology.tap_cell_offset")
            self.append("set_db add_well_taps_cell {TAP_CELL}".format(TAP_CELL=tap_cell))
            self.append("add_well_taps -cell_interval {INTERVAL} -in_row_offset {OFFSET}".format(INTERVAL=interval, OFFSET=offset))
        except KeyError:
            pass
        return True

    def place_pins(self) -> bool:
        fp_consts = self.get_placement_constraints()
        topconst = None  # type: Optional[PlacementConstraint]
        for const in fp_consts:
            if const.type == PlacementConstraintType.TopLevel:
                topconst = const
        if topconst is None:
            self.logger.fatal("Cannot find top-level constraints to place pins")
            return False

        power_pin_layers = self.get_setting("par.generate_power_straps_options.by_tracks.pin_layers")

        const = cast(PlacementConstraint, topconst)
        assert isinstance(const.margins, Margins), "Margins must be defined for the top level"
        fp_llx = const.margins.left
        fp_lly = const.margins.bottom
        fp_urx = const.width - const.margins.right
        fp_ury = const.height - const.margins.top

        pin_assignments = self.get_pin_assignments()
        self.verbose_append("set_db assign_pins_edit_in_batch true")

        promoted_pins = []  # type: List[str]
        # Set the top/bottom layers for promoting macro pins
        self.append(f"set_db assign_pins_promoted_macro_bottom_layer {self.get_stackup().get_metal_by_index(1).name}")
        self.append(f"set_db assign_pins_promoted_macro_top_layer {self.get_stackup().get_metal_by_index(-1).name}")
        self.append('set all_ppins ""')
        for pin in pin_assignments:
            if pin.preplaced:
                # Find the pin object that should be promoted. Only one of the two (driver_pins or load_pins) should be non-empty.
                self.append(f'set ppins [get_db [get_nets {pin.pins}] .driver_pins]')
                self.append(f'if {{$ppins eq ""}} {{set ppins [get_db [get_nets {pin.pins}] .load_pins]}}')
                self.append("lappend all_ppins $ppins")
                # First promote the pin
                self.append("set_promoted_macro_pin -insts [get_db $ppins .inst.name] -pins [get_db $ppins .base_name]")
                # Then set them to don't touch and skip routing
                self.append("set_dont_touch [get_db $ppins .net]")
                self.append("set_db [get_db $ppins .net] .skip_routing true")
            else:
                # TODO: Do we need pin blockages for our layers?
                # Seems like we will only need special pin blockages if the vias are larger than the straps

                cadence_side = None  # type: Optional[str]
                if pin.side is not None:
                    if pin.side == "internal":
                        cadence_side = "inside"
                    else:
                        cadence_side = pin.side
                side_arg = get_or_else(optional_map(cadence_side, lambda s: "-side " + s), "")

                start_arg = ""
                end_arg = ""
                assign_arg = ""
                pattern_arg = ""

                if pin.location is None:
                    start_arg = "-{start} {{ {sx} {sy} }}".format(
                        start="start" if pin.side == "bottom" or pin.side == "right" else "end",
                        sx=fp_urx if pin.side != "left" else fp_llx,
                        sy=fp_ury if pin.side != "bottom" else fp_lly)

                    end_arg = "-{end} {{ {ex} {ey} }}".format(
                        end="end" if pin.side == "bottom" or pin.side == "right" else "start",
                        ex=fp_llx if pin.side != "right" else fp_urx,
                        ey=fp_lly if pin.side != "top" else fp_ury
                    )
                    if pin.layers and len(pin.layers) > 1:
                        pattern_arg = "-pattern fill_optimised"
                    else:
                        pattern_arg = "-spread_type range"
                else:
                    assign_arg = "-assign {{ {x} {y} }}".format(x=pin.location[0], y=pin.location[1])

                layers_arg = ""

                if set(pin.layers or []).intersection(set(power_pin_layers)):
                    self.logger.error("Signal pins will be generated on the same layer(s) as power pins. Double-check to see if intended.")

                if pin.layers is not None and len(pin.layers) > 0:
                    layers_arg = "-layer {{ {} }}".format(" ".join(pin.layers))

                width_arg = get_or_else(optional_map(pin.width, lambda f: "-pin_width {f}".format(f=f)), "")
                depth_arg = get_or_else(optional_map(pin.depth, lambda f: "-pin_depth {f}".format(f=f)), "")

                cmd = [
                    "edit_pin",
                    "-fixed_pin",
                    "-pin", pin.pins,
                    "-hinst", self.top_module,
                    pattern_arg,
                    layers_arg,
                    side_arg,
                    start_arg,
                    end_arg,
                    assign_arg,
                    width_arg,
                    depth_arg
                ]

                self.verbose_append(" ".join(cmd))

        # In case the * wildcard is used after preplaced pins, this will put back the promoted pins correctly.
        self.verbose_append("if {[llength $all_ppins] ne 0} {assign_io_pins -move_fixed_pin -pins [get_db $all_ppins .net.name]}")

        self.verbose_append("set_db assign_pins_edit_in_batch false")
        return True

    def power_straps(self) -> bool:
        """Place the power straps for the design."""
        power_straps_tcl = os.path.join(self.run_dir, "power_straps.tcl")
        self.write_contents_to_path("\n".join(self.create_power_straps_tcl()), power_straps_tcl)
        self.verbose_append("source -echo -verbose {}".format(power_straps_tcl))
        return True

    def place_opt_design(self) -> bool:
        """Place the design and do pre-routing optimization."""
        # First, check that no ports are left unplaced. Exit with error code for user to fix.
        self.append('''
            set unplaced_pins [get_db ports -if {.place_status == unplaced}]
            if {$unplaced_pins ne ""} {
                print_message -error "Some pins remain unplaced, which will cause invalid placement and routing. These are the unplaced pins: $unplaced_pins"
                exit 2
            }
            ''', clean=True)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append('''
            flatten_ilm
            update_constraint_mode -name {name} -ilm_sdc_files {sdc}
            '''.format(name=self.constraint_mode, sdc=self.post_synth_sdc), clean=True)

        # Use place_opt_design V2 (POD-Turbo). Option must be explicitly set only in 22.1.
        if self.version() >= self.version_number("221") and self.version() < self.version_number("231"):
            self.verbose_append("set_db opt_enable_podv2_clock_opt_flow true")

        self.verbose_append("place_opt_design")
        return True

    def clock_tree(self) -> bool:
        """Setup and route a clock tree for clock nets."""
        if len(self.get_clock_ports()) > 0 or len(self.get_setting("vlsi.inputs.custom_sdc_files")) > 0:
            # Fix fanout load violations
            self.verbose_append("set_db opt_fix_fanout_load true")
            # Ignore clock tree when there are no clocks
            # If special cells are specified, explicitly set them instead of letting tool infer from libs
            buffer_cells = self.technology.get_special_cell_by_type(CellType.CTSBuffer)
            if len(buffer_cells) > 0:
                self.append(f"set_db cts_buffer_cells {{{' '.join(buffer_cells[0].name)}}}")
            inverter_cells = self.technology.get_special_cell_by_type(CellType.CTSInverter)
            if len(inverter_cells) > 0:
                self.append(f"set_db cts_inverter_cells {{{' '.join(inverter_cells[0].name)}}}")
            gate_cells = self.technology.get_special_cell_by_type(CellType.CTSGate)
            if len(gate_cells) > 0:
                self.append(f"set_db cts_clock_gating_cells {{{' '.join(gate_cells[0].name)}}}")
            logic_cells = self.technology.get_special_cell_by_type(CellType.CTSLogic)
            if len(logic_cells) > 0:
                self.append(f"set_db cts_logic_cells {{{' '.join(logic_cells[0].name)}}}")
            self.verbose_append("create_clock_tree_spec")
            if self.version() >= self.version_number("221"):
                # Required for place_opt_design v2 (POD-Turbo)
                if bool(self.get_setting("par.innovus.use_cco")):
                    self.verbose_append("clock_opt_design -hold -timing_debug_report")
                else:
                    self.verbose_append("clock_opt_design -cts -timing_debug_report")
            else:
                if bool(self.get_setting("par.innovus.use_cco")):
                    # -hold is a secret flag for ccopt_design (undocumented anywhere)
                    self.verbose_append("ccopt_design -hold -timing_debug_report")
                else:
                    self.verbose_append("clock_design")
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("unflatten_ilm")
        return True

    def add_fillers(self) -> bool:
        """add decap and filler cells"""
        decaps = self.technology.get_special_cell_by_type(CellType.Decap)
        stdfillers = self.technology.get_special_cell_by_type(CellType.StdFiller)

        if len(decaps) == 0:
            self.logger.info("The technology plugin 'special cells: decap' field does not exist. It should specify a list of decap cells. Filling with stdfiller instead.")
        else:
            decap_cells = decaps[0].name
            decap_caps = []  # type: List[float]
            cap_unit = self.get_cap_unit().value_prefix + self.get_cap_unit().unit
            if decaps[0].size is not None:
                decap_caps = list(map(lambda x: CapacitanceValue(x).value_in_units(cap_unit), decaps[0].size))
            if len(decap_cells) != len(decap_caps):
                self.logger.error("Each decap cell in the name list must have a corresponding decapacitance value in the size list.")
            decap_consts = list(filter(lambda x: x.target=="capacitance", self.get_decap_constraints()))
            if len(decap_consts) > 0:
                if decap_caps is None:
                    self.logger.warning("No decap capacitances specified but decap constraints with target: 'capacitance' exist. Add decap capacitances to the tech plugin!")
                else:
                    for (cell, cap) in zip(decap_cells, decap_caps):
                        self.append("add_decap_cell_candidates {CELL} {CAP}".format(CELL=cell, CAP=cap))
                    for const in decap_consts:
                        assert isinstance(const.capacitance, CapacitanceValue)
                        area_str = ""
                        if all(c is not None for c in (const.x, const.y, const.width, const.height)):
                            assert isinstance(const.x, Decimal)
                            assert isinstance(const.y, Decimal)
                            assert isinstance(const.width, Decimal)
                            assert isinstance(const.height, Decimal)
                            area_str = " ".join(("-area", str(const.x), str(const.y), str(const.x+const.width), str(const.y+const.height)))
                        self.verbose_append("add_decaps -effort high -total_cap {CAP} {AREA}".format(
                            CAP=const.capacitance.value_in_units(cap_unit), AREA=area_str))

        if len(stdfillers) == 0:
            self.logger.warning(
                "The technology plugin 'special cells: stdfiller' field does not exist. It should specify a list of (non IO) filler cells. No filler will be added. You can override this with an add_fillers hook if you do not specify filler cells in the technology plugin.")
        else:
            # Decap cells as fillers
            if len(decaps) > 0:
                fill_cells = list(map(lambda c: str(c), decaps[0].name))
                self.append("set_db add_fillers_cells \"{FILLER}\"".format(FILLER=" ".join(fill_cells)))
                # Targeted decap constraints
                decap_consts = list(filter(lambda x: x.target=="density", self.get_decap_constraints()))
                for const in decap_consts:
                    area_str = ""
                    if all(c is not None for c in (const.x, const.y, const.width, const.height)):
                        assert isinstance(const.x, Decimal)
                        assert isinstance(const.y, Decimal)
                        assert isinstance(const.width, Decimal)
                        assert isinstance(const.height, Decimal)
                        area_str = " ".join(("-area", str(const.x), str(const.y), str(const.x+const.width), str(const.y+const.height)))
                    self.verbose_append("add_fillers -density {DENSITY} {AREA}".format(
                        DENSITY=str(const.density), AREA=area_str))
                # Or, fill everywhere if no decap constraints given
                if len(self.get_decap_constraints()) == 0:
                    self.verbose_append("add_fillers")

            # Then the rest is stdfillers
            fill_cells = list(map(lambda c: str(c), stdfillers[0].name))
            self.append("set_db add_fillers_cells \"{FILLER}\"".format(FILLER=" ".join(fill_cells)))
            self.verbose_append("add_fillers")
        return True


    def route_design(self) -> bool:
        """Route the design."""
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("flatten_ilm")

        # Allow express design effort to complete running.
        # By default, route_design will abort in express mode with
        # "WARNING (NRIG-142) Express flow by default will not run routing".
        self.verbose_append("set_db design_express_route true")

        self.verbose_append("route_design")
        return True

    def opt_settings(self) -> str:
        cmds = []
        # Enable auto hold recovery if slack degrades
        cmds.append("set_db opt_post_route_hold_recovery auto")
        # Fix SI-induced slew violations (glitch fixing enabled by default)
        cmds.append("set_db opt_post_route_fix_si_transitions true")
        # Report reasons for not fixing hold
        cmds.append("set_db opt_verbose true")
        # Report reasons for not fixing DRVs
        cmds.append("set_db opt_detail_drv_failure_reason true")
        if self.version() >= self.version_number("221"):  # critical region resynthesis
            # Report failure reasons
            cmds.append("set_db opt_sequential_genus_restructure_report_failure_reason true")
        return "\n".join(cmds)

    def opt_design(self) -> bool:
        """
        Post-route optimization and fix setup & hold time violations.
        -expanded_views creates timing reports for each MMMC view.
        """
        self.append(self.opt_settings())
        self.verbose_append("opt_design -post_route -setup -hold -expanded_views -timing_debug_report")
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("unflatten_ilm")
        return True

    def route_opt_design(self) -> bool:
        """
        Routing and post-route optimization. New flow as default in 23.1.
        Performs more optimization during the routing stage.
        Fixes setup, hold, and DRV violations.
        """
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("flatten_ilm")
        # Allow express design effort to complete running.
        # By default, route_design will abort in express mode with
        # "WARNING (NRIG-142) Express flow by default will not run routing".
        self.verbose_append("set_db design_express_route true")
        self.append(self.opt_settings())
        
        # For Innovus 25.1+ without QRC tech files, use separate route + opt flow
        # route_opt_design requires QRC tech files for its internal postRoute extraction in 25.1
        if self.version() >= self.version_number("251"):
            qrc_tech = self.get_qrc_tech() if hasattr(self, 'get_qrc_tech') else ''
            if qrc_tech == '':
                # No QRC files - use preRoute extraction and traditional separate routing/optimization
                # This avoids the strict QRC requirements of route_opt_design's postRoute extraction
                self.verbose_append("set_db extract_rc_engine pre_route")
                self.verbose_append("route_design")
                # After routing, switch to postRoute extraction for optimization
                self.verbose_append("set_db extract_rc_engine post_route")
                self.verbose_append("opt_design -post_route -timing_debug_report")
                if self.hierarchical_mode.is_nonleaf_hierarchical():
                    self.verbose_append("unflatten_ilm")
                return True
        
        # Standard route_opt_design flow (works for all versions with QRC, and <25.1 without QRC)
        self.verbose_append("route_opt_design -timing_debug_report")
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("unflatten_ilm")
        return True

    def opt_signoff(self) -> bool:
        """
        Signoff timing and optimization.
        Should only be run after all implementation flows to obtain better timing,
        setup/hold fixing, DRV fixing, and power optimization.
        Note: runs Tempus in batch mode.
        """
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("flatten_ilm")

        # Options
        # Set clock fixing cell list
        cell_types = [CellType.CTSBuffer, CellType.CTSInverter, CellType.CTSGate, CellType.CTSLogic]
        cells_by_type = chain(*map(lambda c: self.technology.get_special_cell_by_type(c), cell_types))
        flat_cells = chain(*map(lambda cells: cells.name, cells_by_type))
        regexp = "|".join(flat_cells)
        self.verbose_append(f"set_db opt_signoff_clock_cell_list [lsearch -regexp -all -inline [get_db lib_cells .base_name] {regexp}]")
        # Fix DRVs
        self.verbose_append("set_db opt_signoff_fix_data_drv true")
        self.verbose_append("set_db opt_signoff_fix_clock_drv true")

        # Signoff timing requires remote host setting
        self.verbose_append(f"set_multi_cpu_usage -remote_host 1 -cpu_per_remote_host {self.get_setting('vlsi.core.max_threads')}")
        self.verbose_append("time_design_signoff")

        # Run
        self.verbose_append("opt_signoff -all")

        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("unflatten_ilm")
        return True

    def assemble_design(self) -> bool:
        # TODO: implement the assemble_design step.
        return True

    def write_netlist(self) -> bool:
        # Don't use virtual connects (using colon, e.g. VSS:) because they mess up LVS
        self.verbose_append("set_db write_stream_virtual_connection false")

        # Connect power nets that are tied together
        for pwr_gnd_net in (self.get_all_power_nets() + self.get_all_ground_nets()):
            if pwr_gnd_net.tie is not None:
                self.verbose_append("connect_global_net {tie} -type net -net_base_name {net}".format(tie=pwr_gnd_net.tie, net=pwr_gnd_net.name))

        # Output a flattened Verilog netlist for the design and include physical cells (-phys) like decaps and fill
        self.verbose_append("write_netlist {netlist} -top_module_first -top_module {top} -exclude_leaf_cells -phys -flat -exclude_insts_of_cells {{ {pcells} }} ".format(
            netlist=self.output_netlist_filename,
            top=self.top_module,
            pcells=" ".join(self.get_physical_only_cells())
        ))

        # Output a non-flattened Verilog netlist with physical instances
        self.verbose_append("write_netlist {netlist} -top_module_first -top_module {top} -exclude_leaf_cells -phys -exclude_insts_of_cells {{ {pcells} }} ".format(
            netlist=self.output_physical_netlist_filename,
            top=self.top_module,
            pcells=" ".join(self.get_physical_only_cells())
        ))

        self.verbose_append("write_netlist {netlist} -top_module_first -top_module {top} -exclude_leaf_cells -exclude_insts_of_cells {{ {pcells} }} ".format(
            netlist=self.output_sim_netlist_filename,
            top=self.top_module,
            pcells=" ".join(self.get_physical_only_cells())
        ))

        return True

    def write_gds(self) -> bool:
        map_file = get_or_else(
            optional_map(self.get_gds_map_file(), lambda f: "-map_file {}".format(f)),
            ""
        )

        gds_files = self.technology.read_libs([
            hammer_tech.filters.gds_filter
        ], hammer_tech.HammerTechnologyUtils.to_plain_item)
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            ilm_gds = list(map(lambda ilm: ilm.gds, self.get_input_ilms()))
            gds_files.extend(ilm_gds)

        # If we are not merging, then we want to use -output_macros.
        # output_macros means that Innovus should take any macros it has and
        # just output the cells into the GDS. These cells will not have physical
        # information inside them and will need to be merged with some other
        # step later. We do not care about uniquifying them because Innovus will
        # output a single cell for each instance (essentially already unique).

        # On the other hand, if we tell Innovus to do the merge then it is going
        # to get a GDS with potentially multiple child cells and we then tell it
        # to uniquify these child cells in case of name collisons. Without that
        # we could have one child that applies to all cells of that name which
        # is often not what you want.
        # For example, if macro ADC1 has a subcell Comparator which is different
        # from ADC2's subcell Comparator, we don't want ADC1's Comparator to
        # replace ADC2's Comparator.
        # Note that cells not present in the GDSes to be merged will be emitted
        # as-is in the output (like with -output_macros).
        merge_options = "-output_macros" if not self.get_setting("par.inputs.gds_merge") else "-uniquify_cell_names -merge {{ {} }}".format(
            " ".join(gds_files)
        )

        # If the user has specified the par.inputs.gds_precision_mode as
        # "auto", we make write_stream modify the GDS precision according to
        # the value of par.inputs.gds_precision. If this setting is not
        # specified then we fall back to the default behavior, which is to use
        # the units specified in the LEF. See documentation for write_stream
        # for what this switch does and what valid values are.
        unit = ""
        if (self.get_setting("par.inputs.gds_precision_mode") == "manual"):
            gds_precision = self.get_setting("par.inputs.gds_precision") or ""
            valid_values = [100, 200, 1000, 2000, 10000, 20000]
            if gds_precision in valid_values:
                unit = "-unit %s" % gds_precision
            else:
                self.logger.error(
                    "par.inputs.gds_precision value of \"%s\" must be one of %s" %(
                        gds_precision, ', '.join([str(x) for x in valid_values])));
                return False
        # "auto", i.e. not "manual", means not specifying anything extra.

        self.verbose_append(
            "write_stream -mode ALL -format stream {map_file} {merge_options} {unit} {gds}".format(
            map_file=map_file,
            merge_options=merge_options,
            gds=self.output_gds_filename,
            unit=unit
        ))
        return True

    def write_def(self) -> bool:
        self.verbose_append("write_def {def_file} -floorplan -netlist -routing".format(def_file=self.output_def_path))

        return True

    def write_sdf(self) -> bool:
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.verbose_append("flatten_ilm")

        # Output the Standard Delay Format File for use in timing annotated gate level simulations
        corners = self.get_mmmc_corners()
        if corners:
            # Derive flags for min::type::max
            max_view = next((c for c in corners if c.type is MMMCCornerType.Setup), None)
            max_view_flag = ""
            if max_view is not None:
                max_view_flag = f"-max_view {max_view.name}.setup_view"
            min_view = next((c for c in corners if c.type is MMMCCornerType.Hold), None)
            min_view_flag = ""
            if min_view is not None:
                min_view_flag = f"-min_view {min_view.name}.hold_view"
            typ_view = next((c for c in corners if c.type is MMMCCornerType.Extra), None)
            typ_view_flag = ""
            if typ_view is not None:
                typ_view_flag = f"-typical_view {typ_view.name}.extra_view"
            self.verbose_append(f"write_sdf {max_view_flag} {min_view_flag} {typ_view_flag} {self.run_dir}/{self.top_module}.par.sdf")
        else:
            self.verbose_append(f"write_sdf {self.run_dir}/{self.top_module}.par.sdf".format(run_dir=self.run_dir, top=self.top_module))

        return True

    def write_spefs(self) -> bool:
        # Output a SPEF file that contains the parasitic extraction results
        self.verbose_append("set_db extract_rc_coupled true")
        self.verbose_append("extract_rc")
        corners = self.get_mmmc_corners()
        if corners:
            for corner in corners:
                # Setting up views for all defined corner types: setup, hold, extra
                if corner.type is MMMCCornerType.Setup:
                    corner_type_name = "setup"
                elif corner.type is MMMCCornerType.Hold:
                    corner_type_name = "hold"
                elif corner.type is MMMCCornerType.Extra:
                    corner_type_name = "extra"
                else:
                    raise ValueError("Unsupported MMMCCornerType")

                self.verbose_append("write_parasitics -spef_file {run_dir}/{top}.{cname}.par.spef -rc_corner {cname}.{ctype}_rc".format(run_dir=self.run_dir, top=self.top_module, cname=corner.name, ctype=corner_type_name))

        else:
            self.verbose_append("write_parasitics -spef_file {run_dir}/{top}.par.spef".format(run_dir=self.run_dir, top=self.top_module))

        return True



    @property
    def output_innovus_lib_name(self) -> str:
        return "{top}_FINAL".format(top=self.top_module)

    @property
    def generated_scripts_dir(self) -> str:
        return os.path.join(self.run_dir, "generated-scripts")

    @property
    def open_chip_script(self) -> str:
        return os.path.join(self.generated_scripts_dir, "open_chip")

    @property
    def open_chip_tcl(self) -> str:
        return self.open_chip_script + ".tcl"

    def write_regs(self) -> bool:
        """write regs info to be read in for simulation register forcing"""
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.append('flatten_ilm')
            self.append(self.child_modules_tcl())
        self.append(self.write_regs_tcl())
        if self.hierarchical_mode.is_nonleaf_hierarchical():
            self.append('unflatten_ilm')
        self.ran_write_regs = True
        return True

    def write_design(self) -> bool:
        # Save the Innovus design.
        self.verbose_append("write_db {lib_name} -def -verilog".format(
            lib_name=self.output_innovus_lib_name
        ))

        # Write netlist
        self.write_netlist()

        # Write DEF
        self.write_def()

        # GDS streamout.
        self.write_gds()

        # Write SDF
        self.write_sdf()

        # Write SPEF
        self.write_spefs()

        # Make sure that generated-scripts exists.
        os.makedirs(self.generated_scripts_dir, exist_ok=True)

        self.ran_write_design=True

        return True

    @property
    def ran_write_regs(self) -> bool:
        """The write_regs step sets this to True if it was run."""
        return self.attr_getter("_ran_write_regs", False)

    @ran_write_regs.setter
    def ran_write_regs(self, val: bool) -> None:
        self.attr_setter("_ran_write_regs", val)

    @property
    def ran_write_design(self) -> bool:
        """The write_design step sets this to True if it was run."""
        return self.attr_getter("_ran_write_design", False)

    @ran_write_design.setter
    def ran_write_design(self, val: bool) -> None:
        self.attr_setter("_ran_write_design", val)

    @property
    def ran_write_ilm(self) -> bool:
        """The write_ilm stage sets this to True if it was run."""
        return self.attr_getter("_ran_write_ilm", False)

    @ran_write_ilm.setter
    def ran_write_ilm(self, val: bool) -> None:
        self.attr_setter("_ran_write_ilm", val)

    @property
    def ilm_dir_name(self) -> str:
        return os.path.join(self.run_dir, "{top}ILMDir".format(top=self.top_module))

    def write_ilm(self) -> bool:
        """Run time_design and write out the ILM."""
        self.verbose_append("time_design -post_route")
        self.verbose_append("time_design -post_route -hold")
        self.verbose_append("check_process_antenna")

        # Currently, this assumes auto-power-straps by_tracks, and uses the pg pin layer
        # to determine the top layer in the generated LEF
        assert self.get_setting("par.generate_power_straps_method") == "by_tracks", "Hierarchical write_ilm currently requires auto power_straps by_tracks"
        top_layer = self.get_setting("par.generate_power_straps_options.by_tracks.pin_layers")
        assert len(top_layer) == 1, "Hierarchical write_ilm requires 1 pin layer specified"
        self.verbose_append("write_lef_abstract -5.8 -top_layer {top_layer} -stripe_pins -pg_pin_layers {{{top_layer}}} {top}ILM.lef".format(
            top=self.top_module,
            top_layer=top_layer[0]
        ))
        self.verbose_append("write_ilm -model_type all -to_dir {ilm_dir_name} -type_flex_ilm ilm".format(
            ilm_dir_name=self.ilm_dir_name))
        # Need to append -hierarchical after get_pins in SDCs for parent timing analysis
        for sdc_out in self.output_ilm_sdcs:
            self.append('gzip -d -c {ilm_dir_name}/mmmc/ilm_data/{top}/{sdc_in}.gz | sed "s/get_pins/get_pins -hierarchical/g" > {sdc_out}'.format(
                ilm_dir_name=self.ilm_dir_name, top=self.top_module, sdc_in=os.path.basename(sdc_out), sdc_out=sdc_out))
        self.ran_write_ilm = True
        return True

    def run_innovus(self) -> bool:
        # Quit Innovus.
        self.verbose_append("exit")
        # Create par script.
        par_tcl_filename = os.path.join(self.run_dir, "par.tcl")
        self.write_contents_to_path("\n".join(self.output), par_tcl_filename)
        
        # Convert TCL to Python if using Innovus 25.1+
        use_python = self.version() >= self.version_number("251")
        
        if use_python:
            # Auto-convert TCL to Python
            converter = InnovusTclToPythonConverter()
            tcl_content = "\n".join(self.output)
            python_content = converter.convert_tcl_to_python(tcl_content)
            
            par_py_filename = os.path.join(self.run_dir, "par.py")
            self.write_contents_to_path(python_content, par_py_filename)
            
            self.logger.info(f"Auto-converted par.tcl to par.py for Innovus {self.version()}")
        
        # Make sure that generated-scripts exists.
        os.makedirs(self.generated_scripts_dir, exist_ok=True)
        
        # Create open_chip script pointing to latest (symlinked to post_<last ran step>).
        self.output.clear()
        assert super().do_pre_steps(self.first_step)
        self.append("read_db latest")
        if self.ran_write_design:
            # Because implementation is done, enable report_timing -early/late and SDF writing
            # without recalculating timing graph for each analysis view
            self.append("set_db timing_enable_simultaneous_setup_hold_mode true")
        
        self.write_contents_to_path("\n".join(self.output), self.open_chip_tcl)
        
        with open(self.open_chip_script, "w") as f:
            f.write("""#!/bin/bash
        cd {run_dir}
        source enter
        $INNOVUS_BIN -common_ui -win -files {open_chip_tcl}
                """.format(run_dir=self.run_dir, open_chip_tcl=self.open_chip_tcl))
        os.chmod(self.open_chip_script, 0o755)
        
        # Build args based on version
        if use_python:
            par_py_filename = os.path.join(self.run_dir, "par.py")
            args = [
                self.get_setting("par.innovus.innovus_bin"),
                "-nowin",  # Prevent the GUI popping up.
                "-common_ui",
                "-python", # Use Python mode
                "-files", par_py_filename
            ]
        else:
            # Use TCL for older versions
            args = [
                self.get_setting("par.innovus.innovus_bin"),
                "-nowin",
                "-common_ui",
                "-files", par_tcl_filename
            ]
        
        # Temporarily disable colours/tag to make run output more readable.
        # TODO: think of a more elegant way to do this?
        HammerVLSILogging.enable_colour = False
        HammerVLSILogging.enable_tag = False
        self.run_executable(args, cwd=self.run_dir)  # TODO: check for errors and deal with them
        HammerVLSILogging.enable_colour = True
        HammerVLSILogging.enable_tag = True
        
        # TODO: check that par run was successful
        
        return True

    def create_floorplan_tcl(self) -> List[str]:
        """
        Create a floorplan TCL depending on the floorplan mode.
        """
        output = []  # type: List[str]

        floorplan_mode = str(self.get_setting("par.innovus.floorplan_mode"))
        if floorplan_mode == "manual":
            floorplan_script_contents = str(self.get_setting("par.innovus.floorplan_script_contents"))
            # TODO(edwardw): proper source locators/SourceInfo
            output.append("# Floorplan manually specified from HAMMER")
            output.extend(floorplan_script_contents.split("\n"))
        elif floorplan_mode == "generate":
            output.extend(self.generate_floorplan_tcl())
        elif floorplan_mode == "auto":
            output.append("# Using auto-generated floorplan")
            output.append("plan_design")
            spacing = self.get_setting("par.blockage_spacing")
            bot_layer = self.get_stackup().get_metal_by_index(1).name
            top_layer = self.get_setting("par.blockage_spacing_top_layer")
            if top_layer is not None:
                output.append("create_place_halo -all_blocks -halo_deltas {{{s} {s} {s} {s}}} -snap_to_site".format(
                    s=spacing))
                output.append("create_route_halo -all_blocks -bottom_layer {b} -space {s} -top_layer {t}".format(
                    b=bot_layer, t=top_layer, s=spacing))
        else:
            if floorplan_mode != "blank":
                self.logger.error("Invalid floorplan_mode {mode}. Using blank floorplan.".format(mode=floorplan_mode))
            # Write blank floorplan
            output.append("# Blank floorplan specified from HAMMER")
        return output

    @staticmethod
    def generate_chip_size_constraint(width: Decimal, height: Decimal, left: Decimal, bottom: Decimal, right: Decimal,
                                      top: Decimal, site: str) -> str:
        """
        Given chip width/height and margins, generate an Innovus TCL command to create the floorplan.
        Also requires a technology specific name for the core site
        """

        site_str = "-site " + site

        # -flip -f allows standard cells to be flipped correctly during place-and-route
        return ("create_floorplan -core_margins_by die -flip f "
                "-die_size_by_io_height max {site_str} "
                "-die_size {{ {width} {height} {left} {bottom} {right} {top} }}").format(
            site_str=site_str,
            width=width,
            height=height,
            left=left,
            bottom=bottom,
            right=right,
            top=top
        )

    def generate_floorplan_tcl(self) -> List[str]:
        """
        Generate a TCL floorplan for Innovus based on the input config/IR.
        Not to be confused with create_floorplan_tcl, which calls this function.
        """
        output = []  # type: List[str]

        # Top-level chip size constraint.
        # Default/fallback constraints if no other constraints are provided.
        # TODO snap this to a core site
        chip_size_constraint = self.generate_chip_size_constraint(
            site=self.technology.get_placement_site().name,
            width=Decimal("1000"), height=Decimal("1000"),
            left=Decimal("100"), bottom=Decimal("100"),
            right=Decimal("100"), top=Decimal("100")
        )

        floorplan_constraints = self.get_placement_constraints()
        global_top_layer = self.get_setting("par.blockage_spacing_top_layer") #  type: Optional[str]

        ############## Actually generate the constraints ################
        for constraint in floorplan_constraints:
            # Floorplan names/insts need to not include the top-level module,
            # despite the internal get_db commands including the top-level module...
            # e.g. Top/foo/bar -> foo/bar
            new_path = "/".join(constraint.path.split("/")[1:])

            if new_path == "":
                assert constraint.type == PlacementConstraintType.TopLevel, "Top must be a top-level/chip size constraint"
                margins = constraint.margins
                assert margins is not None
                # Set top-level chip dimensions.
                chip_size_constraint = self.generate_chip_size_constraint(
                    site=self.technology.get_placement_site().name,
                    width=constraint.width,
                    height=constraint.height,
                    left=margins.left,
                    bottom=margins.bottom,
                    right=margins.right,
                    top=margins.top
                )
            else:
                orientation = constraint.orientation if constraint.orientation is not None else "r0"
                if constraint.create_physical:
                    output.append("create_inst -cell {cell} -inst {inst} -location {{{x} {y}}} -orient {orientation} -physical -status fixed".format(
                        cell=constraint.master,
                        inst=new_path,
                        x=constraint.x,
                        y=constraint.y,
                        orientation=orientation
                    ))
                if constraint.type == PlacementConstraintType.Dummy:
                    pass
                elif constraint.type == PlacementConstraintType.SoftPlacement:
                    output.append("create_boundary_constraint -type guide -hinst {name} -rects {{ {x1} {y1} {x2} {y2} }}".format(
                        name=new_path,
                        x1=constraint.x,
                        x2=constraint.x + constraint.width,
                        y1=constraint.y,
                        y2=constraint.y + constraint.height
                    ))
                elif constraint.type == PlacementConstraintType.HardPlacement:
                    output.append("create_boundary_constraint -type region -hinst {name} -rects {{ {x1} {y1} {x2} {y2} }}".format(
                        name=new_path,
                        x1=constraint.x,
                        x2=constraint.x + constraint.width,
                        y1=constraint.y,
                        y2=constraint.y + constraint.height
                    ))
                elif constraint.type == PlacementConstraintType.Overlap:
                    output.append("place_inst {inst} {x} {y} {orientation}{fixed}".format(
                        inst=new_path,
                        x=constraint.x,
                        y=constraint.y,
                        orientation=orientation,
                        fixed=" -fixed" if constraint.create_physical else ""
                    ))
                elif constraint.type in [PlacementConstraintType.HardMacro, PlacementConstraintType.Hierarchical]:
                    output.append("place_inst {inst} {x} {y} {orientation}{fixed}".format(
                        inst=new_path,
                        x=constraint.x,
                        y=constraint.y,
                        orientation=orientation,
                        fixed=" -fixed" if constraint.create_physical else ""
                    ))
                    spacing = self.get_setting("par.blockage_spacing")
                    if constraint.top_layer is not None:
                        current_top_layer = constraint.top_layer #  type: Optional[str]
                    elif global_top_layer is not None:
                        current_top_layer = global_top_layer
                    else:
                        current_top_layer = None
                    if current_top_layer is not None:
                        bot_layer = self.get_stackup().get_metal_by_index(1).name
                        cover_layers = list(map(lambda m: m.name, self.get_stackup().get_metals_below_layer(current_top_layer)))
                        output.append("create_route_halo -bottom_layer {b} -space {s} -top_layer {t} -inst {inst}".format(
                            inst=new_path, b=bot_layer, t=current_top_layer, s=spacing))

                        if(self.get_setting("par.power_to_route_blockage_ratio") < 1):
                            self.logger.warning("The power strap blockage region is smaller than the routing halo region for hard macros. Double-check if this is intended.")

                        place_push_out = round(spacing*self.get_setting("par.power_to_route_blockage_ratio") , 1) # Push the place halo, and therefore PG blockage, further out from route halo so router is aware of straps before entering final routing.

                        output.append("create_place_halo -insts {inst} -halo_deltas {{{s} {s} {s} {s}}} -snap_to_site".format(
                            inst=new_path, s=place_push_out))
                        output.append("set pg_blockage_shape [get_db [get_db hinsts {inst}][get_db insts {inst}] .place_halo_polygon]".format(
                            inst=new_path))
                        output.append("create_route_blockage -pg_nets -layers {{{layers}}} -polygon $pg_blockage_shape".format(layers=" ".join(cover_layers)))

                elif constraint.type == PlacementConstraintType.Obstruction:
                    obs_types = get_or_else(constraint.obs_types, [])  # type: List[ObstructionType]
                    assert '/' not in new_path, "'obstruction' placement constraints must be provided a path directly under the top level"
                    if ObstructionType.Place in obs_types:
                        output.append("create_place_blockage -name {name}_place -area {{{x} {y} {urx} {ury}}}".format(
                            name=new_path,
                            x=constraint.x,
                            y=constraint.y,
                            urx=constraint.x+constraint.width,
                            ury=constraint.y+constraint.height
                        ))
                    if ObstructionType.Route in obs_types:
                        output.append("create_route_blockage -name {name}_route -except_pg_nets -{layers} -spacing 0 -{area_flag} {{{x} {y} {urx} {ury}}}".format(
                            name=new_path,
                            x=constraint.x,
                            y=constraint.y,
                            urx=constraint.x+constraint.width,
                            ury=constraint.y+constraint.height,
                            area_flag="rects" if self.version() >= self.version_number("181") else "area",
                            layers="all {route}" if constraint.layers is None else f'layers {{{" ".join(get_or_else(constraint.layers, []))}}}'
                        ))
                    if ObstructionType.Power in obs_types:
                        output.append("create_route_blockage -name {name}_power -pg_nets -{layers} -{area_flag} {{{x} {y} {urx} {ury}}}".format(
                            name=new_path,
                            x=constraint.x,
                            y=constraint.y,
                            urx=constraint.x+constraint.width,
                            ury=constraint.y+constraint.height,
                            area_flag="rects" if self.version() >= self.version_number("181") else "area",
                            layers="all {route}" if constraint.layers is None else f'layers {{{" ".join(get_or_else(constraint.layers, []))}}}'
                        ))
                else:
                    assert False, "Should not reach here"
        return [chip_size_constraint] + output

    def specify_std_cell_power_straps(self, blockage_spacing: Decimal, bbox: Optional[List[Decimal]], nets: List[str]) -> List[str]:
        """
        Generate a list of TCL commands that build the low-level standard cell power strap rails.
        This will use the -master option to create power straps based on the tapcells in the special cells list.
        The layer is set by technology.core.std_cell_rail_layer, which should be the highest metal layer in the std cell rails.

        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param nets: A list of power net names (e.g. ["VDD", "VSS"]). Currently only two are supported.
        :return: A list of TCL commands that will generate power straps on rails.
        """
        layer_name = self.get_setting("technology.core.std_cell_rail_layer")
        layer = self.get_stackup().get_metal(layer_name)
        results = [
            "# Power strap definition for layer {} (rails):\n".format(layer_name),
            "set_db add_stripes_stacked_via_bottom_layer {}".format(layer_name),
            "set_db add_stripes_stacked_via_top_layer {}".format(layer_name),
            "set_db add_stripes_spacing_from_block {}".format(blockage_spacing)
        ]
        tapcells = self.technology.get_special_cell_by_type(CellType.TapCell)[0].name
        options = [
            "-pin_layer", layer_name,
            "-layer", layer_name,
            "-over_pins", "1",
            "-master", "\"{}\"".format(" ".join(tapcells)),
            "-block_ring_bottom_layer_limit", layer_name,
            "-block_ring_top_layer_limit", layer_name,
            "-pad_core_ring_bottom_layer_limit", layer_name,
            "-pad_core_ring_top_layer_limit", layer_name,
            "-direction", str(layer.direction),
            "-width", "pin_width",
            "-nets", "{ %s }" % " ".join(nets)
        ]
        if bbox is not None:
            options.extend([
                "-area", "{ %s }" % " ".join(map(str, bbox))
            ])
        results.append("add_stripes " + " ".join(options) + "\n")
        return results

    def specify_power_straps(self, layer_name: str, bottom_via_layer_name: str, blockage_spacing: Decimal, pitch: Decimal, width: Decimal, spacing: Decimal, offset: Decimal, bbox: Optional[List[Decimal]], nets: List[str], add_pins: bool, antenna_trim_shape: str) -> List[str]:
        """
        Generate a list of TCL commands that will create power straps on a given layer.
        This is a low-level, cad-tool-specific API. It is designed to be called by higher-level methods, so calling this directly is not recommended.
        This method assumes that power straps are built bottom-up, starting with standard cell rails.

        :param layer_name: The layer name of the metal on which to create straps.
        :param bottom_via_layer_name: The layer name of the lowest metal layer down to which to drop vias.
        :param blockage_spacing: The minimum spacing between the end of a strap and the beginning of a macro or blockage.
        :param pitch: The pitch between groups of power straps (i.e. from left edge of strap A to the next left edge of strap A).
        :param width: The width of each strap in a group.
        :param spacing: The spacing between straps in a group.
        :param offset: The offset to start the first group.
        :param bbox: The optional (2N)-point bounding box of the area to generate straps. By default the entire core area is used.
        :param nets: A list of power nets to create (e.g. ["VDD", "VSS"], ["VDDA", "VSS", "VDDB"],  ... etc.).
        :param add_pins: True if pins are desired on this layer; False otherwise.
        :param antenna_trim_shape: Strategy for trimming strap antennae. {none/stripe}
        :return: A list of TCL commands that will generate power straps.
        """
        # TODO check that this has been not been called after a higher-level metal and error if so
        # TODO warn if the straps are off-pitch
        results = ["# Power strap definition for layer %s:\n" % layer_name]
        results.extend([
            "set_db add_stripes_stacked_via_top_layer {}".format(layer_name),
            "set_db add_stripes_stacked_via_bottom_layer {}".format(bottom_via_layer_name),
            "set_db add_stripes_trim_antenna_back_to_shape {{{}}}".format(antenna_trim_shape),
            "set_db add_stripes_spacing_from_block {}".format(blockage_spacing)
        ])
        layer = self.get_stackup().get_metal(layer_name)
        options = [
            "-create_pins", ("1" if (add_pins) else "0"),
            "-block_ring_bottom_layer_limit", layer_name,
            "-block_ring_top_layer_limit", bottom_via_layer_name,
            "-direction", str(layer.direction),
            "-layer", layer_name,
            "-nets", "{%s}" % " ".join(nets),
            "-pad_core_ring_bottom_layer_limit", bottom_via_layer_name,
            "-set_to_set_distance", str(pitch),
            "-spacing", str(spacing),
            "-switch_layer_over_obs", "0",
            "-width", str(width)
        ]
        # Where to get the io-to-core offset from a bbox
        index = 0
        if layer.direction == RoutingDirection.Horizontal:
            index = 1
        elif layer.direction != RoutingDirection.Vertical:
            raise ValueError("Cannot handle routing direction {d} for layer {l} when creating power straps".format(d=str(layer.direction), l=layer_name))

        if bbox is not None:
            options.extend([
                "-area", "{ %s }" % " ".join(map(str, bbox)),
                "-start", str(offset + bbox[index])
            ])

        else:
            # Just put straps in the core area
            options.extend([
                "-area", "[get_db designs .core_bbox]",
                "-start", "[expr [lindex [lindex [get_db designs .core_bbox] 0] {index}] + {offset}]".format(index=index, offset=offset)
            ])
        results.append("add_stripes " + " ".join(options) + "\n")
        return results

def innovus_global_settings(ht: HammerTool) -> bool:
    """Settings that need to be reapplied at every tool invocation"""
    assert isinstance(ht, HammerPlaceAndRouteTool)
    assert isinstance(ht, CadenceTool)
    ht.create_enter_script()

    # Python sucks here for verbosity
    verbose_append = ht.verbose_append

    # Generic settings
    verbose_append("set_db design_process_node {}".format(ht.get_setting("vlsi.core.node")))
    verbose_append("set_multi_cpu_usage -local_cpu {}".format(ht.get_setting("vlsi.core.max_threads")))

    return True

tool = Innovus

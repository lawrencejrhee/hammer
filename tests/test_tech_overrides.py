import json
import os
import sys

import hammer.config as hammer_config
import hammer.tech as hammer_tech
from hammer.config import HammerJSONEncoder
from hammer.logging import HammerVLSILogging
from hammer.vlsi import HammerVLSISettings
from tests.utils.tech import HasGetTech
from tests.utils.tool import HammerToolTestHelpers


class TestOverrideTechLibraries(HasGetTech):
    def test_nested_file_fields_are_skipped(self, tmpdir, request) -> None:
        name = request.function.__name__
        tech_dir = HammerToolTestHelpers.create_tech_dir(str(tmpdir), name)
        library = {
            "lef_file": os.path.join(str(tmpdir), "pdk", "tech.lef"),
            "spice_model_file": {"path": os.path.join(str(tmpdir), "pdk", "models.spice")},
            "itf_files": {"max_cap": "max.itf", "min_cap": "min.itf"},
            "tluplus_files": {"max_cap": "max.tluplus", "min_cap": "min.tluplus"},
            "provides": [{"lib_type": "technology"}],
        }
        with open(os.path.join(tech_dir, f"{name}.tech.json"), "w") as f:
            f.write(json.dumps({"name": name, "installs": [], "libraries": [library]},
                               cls=HammerJSONEncoder, indent=4))
        sys.path.append(str(tmpdir))
        tech = self.get_tech(hammer_tech.HammerTechnology.load_from_module(name))
        tech.logger = HammerVLSILogging.context("tech")
        tech.cache_dir = tech_dir
        with open(os.path.join(tech_dir, "tech.lef"), "w") as f:
            f.write("VERSION 5.8 ;\n")
        database = hammer_config.HammerDatabase()
        database.update_technology(*tech.get_config())
        HammerVLSISettings.load_builtins_and_core(database)
        database.update_project([{"vlsi.core.technology": name}])
        tech.set_database(database)

        tech.override_tech_libraries()

        lib = tech.config.libraries[0]
        assert lib.lef_file == os.path.join(tech_dir, "tech.lef")
        assert lib.itf_files.max_cap == "max.itf"
        assert lib.tluplus_files.min_cap == "min.tluplus"
        assert lib.spice_model_file.path == os.path.join(str(tmpdir), "pdk", "models.spice")

from hammer.vlsi.submit_command import HammerSlurmSettings, HammerSlurmSubmitCommand


def _srun_args(**settings) -> list:
    cmd = HammerSlurmSubmitCommand()
    cmd.settings = HammerSlurmSettings.from_setting({"srun_binary": "srun", **settings})
    return cmd.srun_args()


def test_num_cpus_runs_one_task_with_that_many_cpus() -> None:
    assert _srun_args(num_cpus=4, partition="eda") == \
        ["srun", "--partition", "eda", "--ntasks", "1", "--cpus-per-task", "4"]


def test_extra_args_still_come_last_and_win() -> None:
    assert _srun_args(num_cpus=4, extra_args=["--ntasks=2"])[-1] == "--ntasks=2"


def test_no_num_cpus_adds_no_task_flags() -> None:
    assert _srun_args() == ["srun"]

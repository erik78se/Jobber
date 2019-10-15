import os
import click
import confuse
import jobbers
from jobbers.abaqus.inpfileparse import traverse
from jobbers.abaqus.licenser import calculate_abaqus_licenses
from jobbers.abaqus.model import SolveJob, GenericJob, Inpfile
from jobbers.abaqus.view import *
from jobbers import config
from jobbers.templating import render_to_out


@click.command()
@click.argument("output", type=click.File("w"))
@click.option(
    "-t",
    "--template",
    required=False,
    type=click.Path(exists=True),
    help="Use custom jinja2 template.",
)
@click.option(
    "-i",
    "--inp",
    required=False,
    type=click.Path(exists=True),
    help="User supplied .inp file for abaqus",
)
def cli(output, template, inp):
    """Processes questions and writes an abaqus slurm to file

    User can override default config in ~/.config/Jobbers/config.yaml
    it will take precedence over package defaults.

    Example usage:

    # Output to stdout

    $ abaqus-jobber  -

    # Output to myjob.job using my-template.j2

    $ abaqus-jobber -t my-template.j2 myjob.job

    # Output to myjob.job using solve.inp as inpfile and my-template.j2 as template

    $ abaqus-jobber -t my-template.j2 -i solve.inp myjob.job
    """

    ###################################
    # Start state, which workflow?
    # <Debug> or <Generic> or <Solve>
    ###################################
    wf = ask_workflow()["workflow"]

    if wf == "solve":

        # Collect "no such file"
        no_such_files = []

        if not inp:
            inp = ask_inp()["inpfile"]

        input_file = Inpfile(filename=inp)
        input_files = []
        input_deck = traverse(input_file, input_files)

        for i in input_files:
            print("Adding input file to job: " + str(i.file))
        print()

        # Visualize missing files
        for i in input_deck:
            if not i.file.is_file():
                print("--- Unable to locate from input file (No such file?) ---")
                print(i)
                no_such_files.append(i)

        # If eigenfrequency == False then We can run with MPI.
        # This check does not seem to work as expected:
        # if not input_deck[0].eigenfrequency:
        if not input_file.eigenfrequency:
            _workflow_solve_parallel(template, input_file, output)

        else:
            _workflow_solve_eigen(template, input_file, output)

    elif wf == "debug":
        _workflow_debug()

    elif wf == "generic":

        _workflow_generic(template, output)

    else:
        raise "Not implemented"


def _workflow_solve(template, inpfile, output):
    """
    The solve workflow.
    """
    solvejob = SolveJob(inpfile)

    # Collect needed resources.
    solvejob.abaqus_module = ask_abaqus_module()

    solvejob.cpus = ask_cpus_int()["cpus"]

    solvejob.abaqus_licenses = ask_abaqus_licenses()

    solvejob.partitions = ask_partitions()["partitions"]

    # Info gathered, dispatch to job rendering
    templates_dir = os.path.join(os.path.dirname(jobbers.abaqus.__file__), "templates")

    if template:
        solvejob.template = template
    else:
        solve_template = config["abaqus"]["solve_template"].get()
        solvejob.template = "{}/{}".format(templates_dir, solve_template)

    render_to_out(solvejob, output)


def _workflow_solve_eigen(template, inpfile, output):
    """
    The solve-eigen sub workflow.
    """
    solvejob = SolveJob(inpfile)

    # If job is a restart read job, ask for restart files.
    if inpfile.restart_read:
        restartfile = ask_restart()
        solvejob.restartjobname = os.path.splitext(os.path.basename(str(restartfile)))[
            0
        ]
        solvejob.inpfile.restart_file = solvejob.restartjobname

    ##################################
    # Collect needed resources.
    ##################################
    solvejob.abaqus_module = ask_abaqus_module()

    solvejob.jobname = ask_jobname(solvejob.inpfile.file.stem)["jobname"]

    solvejob.nodes = 1

    solvejob.gpus = ask_gpus_bool()["gpus"]

    # TODO: This should not be hardcoded here. Cluster config?
    solvejob.ntasks_per_node = 36  # We guess that cores =36 based on cluster sizes

    solvejob.cpus = int(solvejob.nodes * solvejob.ntasks_per_node)

    lics_needed = calculate_abaqus_licenses(solvejob.cpus)

    # solvejob.abaqus_licenses = ask_abaqus_licenses_parallel()

    solvejob.abaqus_licenses = {"license": "abaqus@slurmdbd", "volume": lics_needed}

    # 20190521: Do not ask for scratch at the moment, go with config default /jhacxc
    # solvejob.scratch = ask_scratch()['scratch']
    solvejob.scratch = config["slurm"]["shared_scratch"].get()

    # 20190521: Do not ask for partitions at the moment, go with config defaults /jhacxc
    # solvejob.partitions = ask_partitions()['partitions']
    solvejob.partitions.append(config["slurm"]["default_partition"].get())

    solvejob.timelimit = int(ask_timelimit()["timelimit"]) * 60

    # Ask for masternode mem (GiB), convert to MiB which is Slurm default
    # Note: Multiply by 950 (not 1024) to make sure limit is below memory output of 'slurm -C'
    # TEMPHACK: If GPU is selected, do not ask for mem, set over 1TB to get a GPU node
    if solvejob.gpus:
        solvejob.masternode_mem = 1048576
    else:
        solvejob.masternode_mem = int(
            float(ask_masternode_mem()["memory"]) * float(1024) * 0.95
        )

    ##########################################
    # Info gathered, dispatch to job rendering
    ##########################################

    templates_dir = os.path.join(os.path.dirname(jobbers.abaqus.__file__), "templates")

    if template:
        solvejob.template = template
    else:
        solve_par_template = config["abaqus"]["solve_eigenfrequency_template"].get()
        # solvejob.template = "{}/{}".format(templates_dir, str(solve_par_template))
        solvejob.template = str(pathlib.Path(templates_dir, solve_par_template))

    render_to_out(solvejob, output)


def _workflow_solve_parallel(template, inpfile, output):
    """
    The solve-parallel sub workflow.
    """
    solvejob = SolveJob(inpfile)

    # If job is a restart read job, ask for restart files.
    if inpfile.restart_read:
        restartfile = ask_restart()
        solvejob.restartjobname = os.path.splitext(os.path.basename(str(restartfile)))[
            0
        ]
        solvejob.inpfile.restart_file = solvejob.restartjobname

    # Collect needed resources.
    solvejob.abaqus_module = ask_abaqus_module()

    solvejob.jobname = ask_jobname(solvejob.inpfile.file.stem)["jobname"]

    solvejob.nodes = ask_nodes()["nodes"]

    # TODO: This should not be hardcoded here. Cluster config?
    # SLURM alternative --mincpus <n>  Controls the minimum number of CPUs allocated per node as the number
    # as the number of nodes is set and exclusive mode is used. Only relevant then whe have more type of hardware´s
    solvejob.ntasks_per_node = 36  # We guess that cores =36 based on cluster sizes

    solvejob.cpus = int(solvejob.nodes * solvejob.ntasks_per_node)

    lics_needed = calculate_abaqus_licenses(solvejob.cpus)
    solvejob.abaqus_licenses = {"license": "abaqus@slurmdbd", "volume": lics_needed}

    # solvejob.abaqus_licenses = ask_abaqus_licenses_parallel()

    solvejob.abaqus_licenses = {"license": "abaqus@slurmdbd", "volume": lics_needed}

    # 20190521: Do not ask for scratch at the moment, go with config default /jhacxc
    # solvejob.scratch = ask_scratch()['scratch']
    solvejob.scratch = config["slurm"]["shared_scratch"].get()

    # 20190521: Do not ask for partitions at the moment, go with config defaults /jhacxc
    # solvejob.partitions = ask_partitions()['partitions']
    solvejob.partitions.append(config["slurm"]["default_partition"].get())

    solvejob.timelimit = int(ask_timelimit()["timelimit"]) * 60

    # Ask for masternode mem (GiB), convert to MiB which is Slurm default
    # Note: Multiply by 950 (not 1024) to make sure limit is below memory output of 'slurm -C'
    solvejob.masternode_mem = int(
        float(ask_masternode_mem()["memory"]) * float(1024) * 0.95
    )
    # For distributed jobs, explicitly set worker node limit if defined
    try:
        solvejob.workernode_mem = int(
            float(config["abaqus"]["workernode_mem_default"].get()[0]) * 1024.0 * 0.95
        )
    except confuse.NotFoundError:
        pass

    # Info gathered, dispatch to job rendering
    templates_dir = os.path.join(os.path.dirname(jobbers.abaqus.__file__), "templates")

    if template:
        solvejob.template = template
    else:
        solve_par_template = config["abaqus"]["solve_distributed_template"].get()
        solvejob.template = str(pathlib.Path(templates_dir, solve_par_template))

    render_to_out(solvejob, output)


def _workflow_generic(template, output):
    generic_job = GenericJob()
    generic_job.generic_resources = ask_generic_resources()

    templates_dir = os.path.join(os.path.dirname(jobbers.abaqus.__file__), "templates")

    if template:
        generic_job.template = template
    else:
        generic_job.template = "{}/{}".format(
            templates_dir, "abaqus-generic-template.j2"
        )

    render_to_out(generic_job, output)


def _workflow_debug():
    """ Help the user.
    """
    print("salloc -p debug -N 1")
    print("srun hostname")
    print("exit")


if __name__ == "__main__":
    cli()

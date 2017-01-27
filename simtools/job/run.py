"""Model ensemble run

Examples
--------

Job-model interface

>>> job run -x runscript.sh -p a=2,3,4 b=0,1

The command above run an ensemble of 6 model versions.

By default runscript.sh is called with command line arguments for parameters
and run directory, as specified by --param-arg and --out-arg arguments, which 
are templates formatted at run-time with appropriate values.

>>> job --saveas run.json run -x runscript.sh --param-arg "--{name} {value}" --out-arg "--out {rundir}" --qos short --account megarun

Save arguments to `run.json` file to be used as default arguments in subsequent calls.

>>> job -c run.json run -p a=2,3,4 b=0,1

Load run.json as default argument, and perform ensemble simulation. 

>>> job -m model.py run -p a=2,3,4 b=0,1

Use custom filetype and model definition.

>>> job run -c run.json -- --years 100

The arguments after `--` are formatted with appropriate runtime variables and
passed to the runscript as command line argument.
"""
import argparse
import tempfile
import numpy as np
from simtools.prior import Prior, DiscreteParam
#from simtools.xparams import XParams
from simtools.xrun import XParams, XRun
from simtools import register
from simtools.job.model import model, getmodel
import simtools.job.stats  # register !

# prepare job
# ===========
register_job = register.register_job
# run
# ---

def parse_slurm_array_indices(a):
    indices = []
    for i in a.split(","):
        if '-' in i:
            if ':' in i:
                i, step = i.split(':')
                step = int(step)
            else:
                step = 1
            start, stop = i.split('-')
            start = int(start)
            stop = int(stop) + 1  # last index is ignored in python
            indices.extend(range(start, stop, step))
        else:
            indices.append(int(i))
    return indices

def _typechecker(type):
    def check(string):
        try:
            type(string) # just a check
        except Exception as error:
            print('ERROR:', error.message)
            raise
        return string

# SLURM high-performance computer
slurm = argparse.ArgumentParser(add_help=False)
grp = slurm.add_argument_group('slurm', 
                            description="These options only apply with --submit")
grp.add_argument('--qos', help='queue')
grp.add_argument('--job-name')
grp.add_argument('--account')
grp.add_argument('--walltime')

# 
submit = argparse.ArgumentParser(add_help=False)
grp = submit.add_argument_group("simulation mode (submit, background...)")
#grp.add_argument('--batch-script', help='')
#x = grp.add_mutually_exclusive_group()
grp.add_argument('-s', '--submit', action='store_true', help='submit job to slurm')
grp.add_argument('-t', '--test', action='store_true', 
               help='test mode: print to screen instead of log, run sequentially')
grp.add_argument('-w','--wait', action='store_true', help='wait for job to end')
grp.add_argument('-b', '--array', action='store_true', 
                 help='submit using sbatch --array (faster!), EXPERIMENTAL)')
grp.add_argument('--dry-run', action='store_true', 
                 help='might write a few files, but do not run')
#x.add_argument('--background', 
#                 action='store_true', help='run in the background, do not wait for executation to end')

simu = argparse.ArgumentParser(add_help=False)
grp = simu.add_argument_group("simulation settings")
simu.add_argument('-o','--out-dir', default='out', dest='expdir',
                  help='experiment directory \
                  (params.txt and logs/ will be created, and possibly individual model output directories (each as {rundir})')
simu.add_argument('-a','--auto-dir', action='store_true', 
                 help='{runtag} and {rundir} named according to parameter values instead of {runid}')

x = simu.add_mutually_exclusive_group()
x.add_argument('-p', '--params',
                 type=DiscreteParam.parse,
                 help=DiscreteParam.parse.__doc__,
                 metavar="NAME=SPEC",
                 nargs='*')
x.add_argument('-i','--params-file', help='ensemble parameters file')
simu.add_argument('-j','--id', type=_typechecker(parse_slurm_array_indices), dest='runid', 
                 metavar="I,J...,START-STOP:STEP,...",
                 help='select one or several ensemble members (0-based !), \
slurm sbatch --array syntax, e.g. `0,2,4` or `0-4:2` \
    or a combination of these, `0,2,4,5` <==> `0-4:2,5`')
simu.add_argument('--include-default', 
                  action='store_true', 
                  help='also run default model version (with no parameters)')

run = argparse.ArgumentParser(add_help=False, parents=[model, simu, submit, slurm],
                              description=__doc__)


# sub
_slurmarray = argparse.ArgumentParser(add_help=False, parents=[model, simu])
_slurmarray_defaults = {a.dest:a.default for a in _slurmarray._actions}  # default arguments


def _autodir(params):
    " create automatic directory based on list of Param instances"
    raise NotImplementedError()

def run_post(o):
    model = getmodel(o)  # default model

    if o.params_file:
        xparams = XParams.read(o.params_file)
    elif o.params:
        prior = Prior(o.params)
        xparams = prior.product() # only product allowed as direct input
        #update = {p.name:p.value for p in o.params}
    else:
        xparams = XParams(np.empty((0,0)), names=[])
        o.include_default = True

    xrun = XRun(model, xparams)
    
    if o.runid:
        indices = parse_slurm_array_indices(o.runid)
    else:
        indices = np.arange(xparams.size)

    # test: run everything serially
    if o.test:
        rundir = '--auto' if o.auto_dir else None
        for i in indices:
            xrun.run(runid=i, expdir=o.expdir, background=False, rundir=rundir, dry_run=o.dry_run)
        if o.include_default:
            xrun.run(expdir=o.expdir, background=False, rundir=rundir, dry_run=o.dry_run)
        o.wait = False

    # array: create a parameterized "job" command [SLURM]
    elif o.array:
        # input via params file
        if not os.path.exists(o.expdir):
            os.makedirs(o.expdir)
        params_file = os.path.join(o.expdir, 'params.txt')
        xrun.params.write(params_file)

        # prepare job command: runid and params passed by slurm
        cfg = o.__dict__.copy()
        del cfg["params"] # passed by file
        del cfg["params_file"] # see below
        del cfg["runid"] # see below

        # write command based on namespace state
        file = tempfile.mktemp(dir=o.expdir, prefix='job.run-array.', suffix='.json')
        write_config(cfg, file, defaults=_slurmarray_defaults, diff=True, name="run")
        template = "{job} -c {config_file} run --id $SLURM_ARRAY_TASK_ID --params-file {params_file}"
        command = template.format(job="job", config_file=file, params_file=params_file) 
        #FIXME: job may be a full path `/path/to/job run` or called via `[/path/to/]python job run`
        #TODO: maybe figure out something?

        # slurm-related options are passed directyl
        slurm_opt = {a.dest:a.default for a in slurm._actions if a.default is not None}
        slurm_opt["array"] = o.runid or "{}-{}".format(0, xparams.size-1)

        if o.dry_run:
            print(slurm_opt)
            print(command)
            return

        p = submit_job(command, **slurm_opt)

    # the default
    else:
        p = xrun.batch(indices=indices, submit=o.submit, 
                   expdir=o.expdir, autodir=o.auto_dir, 
                       include_default=o.include_default) #, output=o.log_out, error=o.log_err)

    if o.wait:
        p.wait()

    return


register_job('run', run, run_post, help='run model (single version or ensemble)')

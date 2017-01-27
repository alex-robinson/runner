from __future__ import print_function, absolute_import
import difflib
import subprocess
import os
import json
from simtools.tools import parse_val
from simtools.submit import submit_job
#from simtools.model.generic import get_or_make_filetype

ARG_TEMPLATE = "--{name} {value}" # by default 
OUT_TEMPLATE = "--out {rundir}" # by default 


class Param(object):
    """default parameter --> useful to specify custom I/O formats
    """
    def __init__(self, name, default=None, help=None, value=None, **kwargs):
        """
        name : parameter name
        default : default value, optional
        help : help (e.g. to provide for argparse), optional
        **kwargs : any other attribute required for custom file formats
            or to define prior distributions.
        """
        self.name = name
        self.default = default
        self.value = value if value is not None else default
        self.help = help
        self.__dict__.update(kwargs)

    #def __repr__(self):
    #    return "{cls}(name={name},default={default},value={value})".format(cls=type(self).__name__, **self.__dict__)

    def __str__(self):
        return "{name}={value}".format(name=self.name, value=self.value)

    @classmethod
    def parse(cls, string):
        name, value = string.split('=')
        return cls(name, parse_val(value))

    def tojson(self, **kwargs):
        return json.dumps(self.__dict__)
        #return json.dumps(str(self))


class ParamsFile(object):
    """Parent class for the parameters
    """
    def dumps(self, params):
        raise NotImplementedError()

    def loads(self, string):
        raise NotImplementedError()

    def dump(self, params, f):
        f.write(self.dumps(params))

    def load(self, f):
        return self.loads(f.read())


# Model instance
# ==============
class Model(object):
    """Generic model configuration
    """
    def __init__(self, executable, args=None, params=None, 
                 arg_template=ARG_TEMPLATE, out_template=OUT_TEMPLATE, 
                 filename=None, filetype=None):
        """
        executable : runscript
        args : [str] or str, optional
            list of command arguments to pass to executable. They may contain
            formattable patterns {rundir}, {runid}, {runtag}. Typically run directory
            or input parameter file.
        params : [Param], optional
            list of model parameters to be updated with modified params
            If params is provided, strict checking of param names is performed during 
            update, with informative error message.
        arg_template : [str] or str, optional
            Indicate parameter format for command-line with placeholders `{name}` and 
            `{value}`. By default `["--{name}", "{value}"]`, but note that any field
            in parameter definition can be used. Set to None or empty list to avoid
            passing parameters via the command-line.
        out_template : [str] or str, optional
            indicate how the output directory is passed to the model
        filename : str, optional
            By default parameters are provided as command-line arguments but if file
            name is provided they will be written to file. Path is relative to rundir.
        filetype : ParamsFile instance or anything with `dump` method, optional
        """
        self.executable = executable
        if isinstance(args, basestring):
            args = args.split()
        self.args = args or []
        self.params = params or []
        self.strict = len(self.params) > 0  
        if isinstance(arg_template, basestring):
            arg_template = arg_template.split()
        self.arg_template = arg_template or []
        if isinstance(out_template, basestring):
            out_template = out_template.split()
        self.out_template = out_template or []
        self.filename = filename 
        self.filetype = filetype

        if filename:
            if filetype is None: 
                raise ValueError("need to provide FileType with filename")
            if not hasattr(filetype, "dumps"):
                raise TypeError("invalid filetype: no `dumps` method: "+repr(filetype))

        #if not hasattr(filetype, "dumps") 
        #self._check_paramsio()
        #self.filetype = get_or_make_filetype(filetype)

    #def _check_paramsio(self):
    #    """check default params or possibly read from file
    #    """
    #    if not hasattr(self.filetype, 'dumps'):
    #        self.filetype = get_or_make_filetype(self.filetype)

    #    # read default params
    #    if not isinstance(self.params, list):
    #        if isinstance(self.params, basestring):
    #            self.params = self.filetype.load(open(self.params))

    #        elif isinstance(self.params, dict):
    #            self.params = [Param(k, self.params[k]) for k in self.params]

    #        elif self.params is None:
    #            self.params = []

    #        else:
    #            raise ValueError("invalid format for params_default:"+repr(self.params))
    #    else:
    #        for p in self.params:
    #            if not hasattr(p, 'name') or not hasattr(p, 'value'):
    #                raise TypeError('model params have wrong type:'+repr(p))


    def update(self, params_kw):
        """Update parameter from ensemble
        """
        names = [p.name for p in self.params]

        for name in params_kw:
            value = params_kw[name]

            # update existing parameter
            if name in names:
                i = names.index(name)
                self.params[i].value = value

            # if no parameter found, never mind, may check or not
            else:
                if self.strict:
                    print("Available parameters:"," ".join(names))
                    suggestions = difflib.get_close_matches(name, names)
                    if suggestions:
                        print("Did you mean: ", ", ".join(suggestions), "?")
                    raise ValueError("unknown parameter:"+repr(name))
                else:
                    self.params.append(Param(name, value=value))


    def setup(self, rundir):
        """Write param file to rundir if necessary
        """
        if not os.path.exists(rundir):
            os.makedirs(rundir)
        if self.filename:
            fname = os.path.join(rundir, self.filename)
            with open(fname, "w") as f:
                self.filetype.dump(self.params, f)


    def command(self, context=None):
        """
        context : dict of experiment variables such as `rundir` and `runid`, which 
            maybe used to format some of the commands before passing to Popen.
        """
        args = [self.executable] + self.out_template + self.args

        # prepare modified command-line arguments with appropriate format
        for p in self.params:
            for c in self.arg_template:
                args.append(c.format(**p.__dict__))

        # format command string with `rundir`, `runid` etc.
        context = context or {}

        # replace patterns such as {runid} in command
        for i, arg in enumerate(args):
            args[i] = arg.format(**context)

        return args


    def run(self, context=None, **kwargs):
        """Popen(Model.command(context), **kwargs)
        """
        args = self.command(context)
        return subprocess.Popen(args, **kwargs)


    def submit(self, context=None, **kwargs):
        """Slurm(Model.command(context), **kwargs)
        """
        args = self.command(context)
        return submit_job(" ".join(args), **kwargs)

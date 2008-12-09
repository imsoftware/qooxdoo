#!/usr/bin/env python

################################################################################
#
#  qooxdoo - the new era of web development
#
#  http://qooxdoo.org
#
#  Copyright:
#    2006-2008 1&1 Internet AG, Germany, http://www.1und1.de
#
#  License:
#    LGPL: http://www.gnu.org/licenses/lgpl.html
#    EPL: http://www.eclipse.org/org/documents/epl-v10.php
#    See the LICENSE file in the project's top-level directory for details.
#
#  Authors:
#    * Thomas Herchenroeder (thron7)
#
################################################################################

import os, sys, re, types, string, copy
import simplejson

from generator.config.ExtMap import ExtMap

console = None

class Job(object):

    # keys of the job data map
    EXTEND_KEY   = "extend"
    RUN_KEY      = "run"
    LET_KEY      = "let"
    LIBRARY_KEY  = "library"
    RESOLVED_KEY = "resolved"
    OVERRIDE_KEY = "__override__"
    OVERRIDE_TAG = "="    # tag for key names, to protect on merging
    OVERRIDE_TAG_REGEXP = re.compile(r'^\%s(.*)$' % OVERRIDE_TAG)  # identify tag and extract orig. key
    KEYS_WITH_JOB_REFS  = [RUN_KEY, EXTEND_KEY]
    MACRO_SPANNING_REGEXP = re.compile(r'^\$\{\w+\}$')  # e.g. "${PATH}"
    JSON_SCALAR_TYPES   = (types.StringTypes, types.IntType, types.LongType, types.FloatType,
                           types.BooleanType, types.NoneType)


    def __init__(self, name, data, console_, config=None):
        global console
        self.name    = name
        self._console= console_
        self._data   = data
        self._config = config

        console      = console_


    def mergeJob(self, sourceJob):
        "merges another job into self"

        sData = sourceJob.getData()
        target= self.getData()

        self.deepJsonMerge(sData, target)


    def mergeValues(self, source, target):
        '''merges source into target;
           assumes no JobMergeValue be passed, all macros expanded'''

        if isinstance(target, types.ListType):
            return self.listMerge(source, target)
        elif isinstance(target, types.DictType):
            return self.deepJsonMerge(source, target)
        else:  # scalar value
            return target  # first val overrules
        

    def fixNameTags(self):
        
        visitor = self.getDataVisitor()
        for map in visitor:
            for key in map.keys():
                mo = Job.OVERRIDE_TAG_REGEXP.search(key)
                if mo:
                    # remove tag from key
                    cleankey = mo.group(1)  # pick the original key
                    map[cleankey] = map[key]
                    del map[key]
                    # add to override key
                    if not Job.OVERRIDE_KEY in map:
                        map[Job.OVERRIDE_KEY] = []
                    map[Job.OVERRIDE_KEY].append(cleankey)


    def resolveExtend(self, entryTrace=[], cfg=None):
        # resolve the 'extend' entry of a job
        config = cfg or self._config

        if self.hasFeature(self.RESOLVED_KEY):
            return

        self.includeGlobalLet() # make sure potential global let is included first

        if self.hasFeature("extend"):
            # loop through 'extend' entries
            extends = self.getFeature("extend")
            for entry in extends:
                # cyclic check: have we seen this already?
                if entry in entryTrace:
                    raise RuntimeError, "Extend entry already seen: %s" % str(entryTrace+[self.name,entry])
                
                entryJob = self._getJob(entry, config)
                if not entryJob:
                    raise RuntimeError, "No such job: \"%s\" (trace: %s)" % (entry, entryTrace+[self.name])

                # make sure this entry job is fully resolved in its context
                entryJob.resolveExtend(entryTrace + [self.name], config)

                # now merge the fully expanded job into the current job
                self.mergeJob(entryJob)

        self.setFeature(self.RESOLVED_KEY, True)


    ##                                                                              
    # resolveRun -- resolve the 'run' key in jobs
    #                                                                               
    # @param     self     (IN) self
    # @return    joblist  (OUT) list of replacement jobs
    # @exception RuntimeError  'resolved' key missing in a job
    #
    # DESCRIPTION
    #  The 'run' key of a job is a list of jobs to be run in its place, e.g.
    #  'run' : ['jobA', 'jobB']. This indicates how the resolution of the key is
    #  done:
    #  - for each job in the 'run' list, a new job is created ("synthetic jobs")
    #  - the original job serves as a template so the new jobs get all the
    #    settings of the original job (apart from the 'run' key)
    #  - an 'extend' key is set with the particular subjob as its
    #    only member (assuming any original 'extend' key has already been
    #    resolved). - This way all the new jobs can be run as regular jobs,
    #    essentially performing the task of the referenced subjob.
    #  - in the list of synthetic jobs is returned
    #
    # CAVEAT
    #  Unlike other 'resolve*' methods in this class (which are self-modifying),
    #  this one is functional and non-destructive (ok, it objectifies the 'run' list)!
    ##
    def resolveRun(self, cfg=None):
        config = cfg or self._config
        subJobs = []
        
        job     = self
        if not job.hasFeature("run"):
            return [job]
        else:
            for subjob in job.getFeature("run"):
                
                subjobObj = self._getJob(subjob, config)
                if not subjobObj:
                    raise RuntimeError, "No such job: \"%s\"" % subjob
                # make new job map job::subjob as copy of job, but extend[subjob]
                newjobname = self.name + self._config.COMPOSED_NAME_SEP + \
                             subjobObj.name.replace(self._config.NS_SEP, self._config.COMPOSED_NAME_SEP)
                newjob     = job.clone()
                newjob.name= newjobname
                newjob.removeFeature('run')       # remove 'run' key
                
                # we assume the initial 'run' job has already been resolved, so
                # we reset it here and set the 'extend' to the subjob
                if newjob.hasFeature('resolved'): 
                    newjob.removeFeature('resolved')
                else:
                    raise RuntimeError, "Cannot resolve 'run' key before 'extend' key"
                newjob.setFeature('extend', [subjobObj]) # extend subjob
                
                # add to config
                self._config.addJob(newjobname, newjob)
                
                # add to job list
                subJobs.append(newjob)
                
            job.setFeature('run', subJobs)   # overwrite with list of Jobs (instead of Strings)

        return subJobs


    def resolveMacros(self):
        self.includeGlobalLet() # make sure potential global let is included
        if self.hasFeature(self.LET_KEY):
            # exand macros in the let
            letMap = self.getFeature(self.LET_KEY)
            letMap = self._expandMacrosInLet(letMap)
            self.setFeature(self.LET_KEY, letMap)
            
            # separate strings from other values
            letmaps = {}
            letmaps['str'] = {}
            letmaps['bin'] = {}
            for k in letMap:
                if isinstance(letMap[k], types.StringTypes):
                    letmaps['str'][k] = letMap[k]
                else:
                    letmaps['bin'][k] = letMap[k]
                    
            # apply dict to other values
            self._expandMacrosInValues(self._data, letmaps)


    def includeGlobalLet(self, additionalLet=None):
        #import pydb; pydb.debugger()
        newlet = self.mapMerge(self.getFeature(self.LET_KEY,{}),{}) # init with local let
        if additionalLet:
            newlet = self.mapMerge(additionalLet, newlet)
        global_let = self._config.get(self.LET_KEY,False)
        if global_let:
            newlet = self.mapMerge(global_let, newlet)

        if newlet:
            self.setFeature(self.LET_KEY, newlet) # set cumulative let value

    
    def _getJob(self, job, cfg=None):
        '''search a job in given, then in original config'''
        config = cfg or self._config
        entryJob = config.getJob(job)  # Config.getJob() handles string/Job polymorphism of 'entry' and returns Job object
        if not entryJob:
            if config != self._config:  # try own config
                entryJob = self._config.getJob(job)

        return entryJob

    def _expandString(self, s, mapstr, mapbin):
        assert isinstance(s, types.StringTypes)
        if s.find(r'${') == -1:  # optimization: no macro -> return
            return s
        macro = ""
        sub   = ""
        possiblyBin = re.match(r'^\${(.*)}$', s)   # look for '${...}' as a bin replacement
        if possiblyBin:
            macro = possiblyBin.group(1)
        if macro and (macro in mapbin.keys()):
            replval = mapbin[macro]
            if isinstance(replval, types.DictType):
                sub = copy.deepcopy(replval)  # make sure macro values are not affected during value merges later
            else:
                sub = replval   # array references are ok for now
        else:
            templ = string.Template(s)
            sub = templ.safe_substitute(mapstr)
        return sub


    def _expandMacrosInValues(self, data, maps):
        """ apply macro expansion on arbitrary values; takes care of recursive data like
            lists and dicts; only actually applies macros when a string is encountered on 
            the way (look for calls to _expandString())"""
        result = data  # intialize result
        
        # arrays
        if isinstance(data, types.ListType):
            for e in range(len(data)):
                enew = self._expandMacrosInValues(data[e], maps)
                if enew != data[e]:
                    console.debug("expanding: %s ==> %s" % (str(data[e]), str(enew)))
                    data[e] = enew
                    
        # dicts
        elif isinstance(data, types.DictType):
            for e in data.keys(): # have to use keys() explicitly since i modify data in place
                # expand in values
                enew = self._expandMacrosInValues(data[e], maps)
                if enew != data[e]:
                    console.debug("expanding: %s ==> %s" % (str(data[e]), str(enew)))
                    data[e] = enew

                # expand in keys
                if ((isinstance(e, types.StringTypes) and
                        e.find(r'${')>-1)):
                    enew = self._expandString(e, maps['str'], {}) # no bin expand here!
                    data[enew] = data[e]
                    del data[e]
                    console.debug("expanding key: %s ==> %s" % (e, enew))

        # JobMergeValues
        elif isinstance(data, JobMergeValue):
            # macro-expand and merge further
            source = self._expandMacrosInValues(data.val1, maps)
            target = self._expandMacrosInValues(data.val2, maps)
            result = self.mergeValues(source, target)

        # strings
        elif isinstance(data, types.StringTypes):
            result = self._expandString(data, maps['str'], maps['bin'])

        # leave everything else alone
        else:
            result = data

        return result


    def _expandMacrosInLet(self, letDict):
        """ do macro expansion within the "let" dict """

        keys = letDict.keys()
        for k in keys:
            kval = letDict[k]
            
            # construct a temp. dict of translation maps, for later calls to _expand* funcs
            # wpbasti: Crazy stuff: Could be find some better variable names here. Seems to be optimized for size already ;)
            if isinstance(kval, types.StringTypes):
                kdicts = {'str': {k:kval}, 'bin': {}}
            else:
                kdicts = {'str': {}, 'bin': {k:kval}}
                
            # cycle through other keys of this dict
            for k1 in keys:
                if k != k1: # no expansion with itself!
                    enew = self._expandMacrosInValues(letDict[k1], kdicts)
                    if enew != letDict[k1]:
                        console.debug("expanding: %s ==> %s" % (k1, str(enew)))
                        letDict[k1] = enew
        return letDict


    def getData(self):
        return self._data

    def getDataVisitor(self):
        data = self.getData()
        assert isinstance(data, types.DictType)

        def visitor(map):
            yield map
            for key in map:
                if isinstance(map[key], types.DictType):
                    for map1 in visitor(map[key]):
                        yield map1

        return visitor(data)


    def getConfig(self):
        return self._config

    def setConfig(self, config):
        self._config = config


    def clone(self):
        return Job(self.name, self._data.copy(), self._console, self._config)

    def hasFeature(self, feature):
        return self._data.has_key(feature)

    def setFeature(self, feature, value):
        self._data[feature]=value

    def get(self, feature, default=None):
        return self.getFeature(feature, default)

    def getFeature(self, feature, default=None):
        dataMap = ExtMap(self._data)
        return dataMap.get(feature, default)

    def removeFeature(self, feature):
        if feature in self._data:
            del self._data[feature]


    def deepJsonMerge(self, source, target):

        def isString(s):
            return isinstance(s, types.StringTypes)
        def isSpanningMacro(m):
            return self.MACRO_SPANNING_REGEXP.search(m)

        if not isinstance(source, types.DictType):
            raise TypeError, "Wrong argument to deepJsonMerge (must be Dict)"

        override_keys = []
        if self.OVERRIDE_KEY in target:
            override_keys = target[self.OVERRIDE_KEY]
            assert isinstance(override_keys, types.ListType)

        for key in source:
            if key == self.OVERRIDE_KEY:  # don't touch meta key
                continue

            elif key in target:  # we have to merge values
                # skip protected keys
                if key in override_keys:
                    continue

                # treat spanning macros (which can represent data structures), and JobMergeValues
                elif ((isString(source[key]) and isSpanningMacro(source[key])) or
                      (isString(target[key]) and isSpanningMacro(target[key])) or
                      isinstance(source[key], JobMergeValue)                   or
                      isinstance(target[key], JobMergeValue)
                     ):
                    # insert an intermediate object, which is resolved when macros are resolved
                    target[key] = JobMergeValue(source[key], target[key])

                # treat "let" specially
                # cruft: this should actually be done in mergeJob(), but it's easier here
                elif key == self.LET_KEY:
                    target[key] = self.mapMerge(source[key], target[key])

                # merge arrays rather than shadowing
                elif isinstance(source[key], types.ListType):
                    # equality problem: in two arbitrary lists, i have no way of telling 
                    # whether any pair of elements is somehow related (e.g. specifies the
                    # same library), and i can't do recursive search here, with some 
                    # similarity reasoning, can i. therefore: non-equal elements are just
                    # considered unrelated.
                    target[key] = self.listMerge(source[key],target[key])
                
                # merge dicts rather than shadowing
                elif isinstance(source[key], types.DictType):
                    # assuming schema-conformance of target[key] as well
                    # recurse on the sub-dicts
                    self.deepJsonMerge(source[key], target[key])
                    #target[key] = self.mapMerge(source[key],target[key])

                else:
                    pass  # leave target key alone
            else:
                target[key] = source[key]

        return target


    def mapMerge(self, source, target):
        """merge source map into target, but don't overwrite existing
           keys in target (unlike target.update(source))"""
        t = source.copy()
        t.update(target)  # target keys take precedence
        return t


    def listMerge(self, source, target):
        """merge source list with target list (currently append),
           avoiding duplicates"""
        t = []
        for e in source:
            if not e in target:
                t.append(e)
        return target + t



# -- a helper class to represent delayed merge values --------------------------

class JobMergeValue(object):

    def __init__(self, val1, val2):
        self.val1 = val1
        self.val2 = val2




from comtypes.client import CreateObject
import os
from multiplierz.mzAPI import mzScan, mzFile as mzAPImzFile
from collections import defaultdict
import warnings

__author__ = 'William Max Alexander'

debug = True

# The underlying Clearcore library uses cycle, experiment and sample numbers
# as indices, thus they're zero-indexed, while the ABSCIEX data extractor and
# other tools present these as one-indexed (biologist-friendly I guess?).
# For lack of a less kludgy solution I'm declaring that each of these should
# be decremented by one whenever they appear in a call to the COM object.

# And incremented when they arrive from the same?



# DEV NOTE: Attempts to reconcile access to the ABSCIEX idea of how MS works
# with the abstract RAW-esque way that it works keep hitting difficulties. It
# looks like the best solution to that would be to have two separate WIFF
# mzFile variants, one of which abstracts over to provide a RAW-like
# interface (nicely compatible, hopefully, interleaving experiments by cycle)
# and the other which demands sample/experiment specifications rigorously
# (making no attempt to push those under the rug, as the current
# implementation does.)

# DEV NOTE: _explicit_numbering will not keep track of "current" sample and
# experiment numbers at all, and require each to be set explicitly on each
# relevant call.  _implicit_numbering will derive what sample and experiment
# to access at each call.  The underlying COM object does what it can to 
# avoid unnecessary sample/experiment switching, but that will be left out
# of mzFile-level code for the time being.

# _explicit_numbering also has decrement-to-zero-indexed duty.




# Some terminology: "cycle" should mean a given run through all experiments,
# 'scan' should mean a specific measured spectrum, numbered in order of acquisition.

#def scan_from_exp_cycle(exp_count, experiment, cycle):
    #return (exp_count*(cycle-1)) + experiment
#def exp_cycle_from_scan(exp_count, scan):
    #return scan / exp_count, (scan % exp_count)+1 # Experiment, cycle
    
# These should both work on 1-indexed numbers,
# and be inverses of each other for a given exp_count.
def scan_from_exp_cycle(exp_count, experiment, cycle):
    return (exp_count*(cycle-1)) + (experiment-1)
def exp_cycle_from_scan(exp_count, scan):
    return (scan % exp_count)+1, (scan / exp_count)+1

class mzFile_implicit_numbering(mzAPImzFile):
    """
    mzAPI interface class for WIFF files.
    
    When initialized, the 'sample' argument sets what sample will be accessed
    by this instance of the object; all calls will pertain only to data from
    that sample. If not specified, this defaults to sample 1. To switch
    samples, change the .sample attribute appropriately.
    
    Scans in a WIFF file are indexed according to time, and scans from
    experiment 0 are assumed to be MS1-level scans.  All other experiments
    are assumed to be MS2-level scans.  So, scan number 1 will generally be an
    MS1 scan, scans 2-<number of experiments-minus-2> will be MS2 scans, and so
    on.  .scan_info() will return information on which particular scans are on
    what level throughout the file.
    
    In order to access scans by experiment and sample number explicitly, initialize
    mzFile with the 'experiment_numbering' argument set to True.
    """
    
    def __init__(self, datafile, sample = 1, **etc):
        self.data_file = datafile
        self.file_type = 'wiff'
        
        self.data = mzFile_explicit_numbering(datafile,
                                              sample = sample,
                                              experiment = 1)
        
        self.sample = sample
        self.exp_num = self.data.source.GetExperiments(sample-1)

        scans_present = [(c, e) for c, e, s in zip(*self.data.scan_info())[2] if s == self.sample]
        self.make_explicit = dict(enumerate(scans_present))
        self.make_implicit = dict((y, x) for x, y in self.make_explicit.items())
        
    def scan(self, scan, **kwargs):
        if isinstance(scan, float):
            if scan != int(scan):
                raise RuntimeError, ("Scan time specification is ambiguous "
                                     "with implicit experiment numbering; use "
                                     "explicit_numbering mode mzFile.")
            else:
                scan = int(scan)
                
        #experiment = (scan-1) % self.exp_num
        # experiment, cycle = exp_cycle_from_scan(self.exp_num, scan)
        experiment, cycle = self.make_explicit[scan]
        return self.data.scan(cycle, experiment = experiment, sample = self.sample,
                              **kwargs)
    
    
    def scan_info(self, start_scan = 0, stop_scan = 999999, start_mz = 0, stop_mz = 999999):
        exp_info = self.data.scan_info(start_scan, stop_scan, sample = self.sample)

        # scan_from_exp_cycle(self.exp_num, experiment, cycle)
        return [(rt, precM, self.make_implicit[cycle, experiment], level, centroid)
                for (rt, precM, (cycle, experiment, sample), level, centroid)
                in exp_info
                if sample == self.sample]
    
    def xic(self, start_time = 0, end_time = 999999, start_mz = 0, end_mz = 2000, filters = None):
        """
        Gets the eXtracted Ion Chromatogram of the given time- and mz-range.
        
        The target experiment is assumed to be 0, i.e., MS1-level scans.
        """
    
        return self.data.xic(start_time, end_time, start_mz, end_mz, 
                             sample = self.sample, experiment=1, filters = filters)
    
    def time_range(self):
        """
        Gets the total retention-time range for the data.
        """
        
        return self.data.time_range(self.sample)
    
    def scan_range(self):
        # Source gives cycle count.
        start, stop = self.data.scan_range(sample = self.sample)
        return start, len(self.make_implicit)
    
    def scan_for_time(self, rt):
        """
        Gets the scan index for the specified retention time.
        """
    
        warnings.warn('.scan_for_time() on implicitly-numbered WIFF files '
                      'may not currently work; GetIndexOfRT uses ambiguous exp number.')
        self.data.scan_for_time(rt, sample = self.sample)
        
    def filters(self):
        return self.data.filters()
    
    def headers(self):
        return self.data.scan_info()
        

class mzFile_explicit_numbering(mzAPImzFile):
    """
    mzAPI interface class for WIFF files.
    
    Scans in a WIFF file are indexed by sample, experiment, and cycle.  Briefly,
    each sample corresponds to a MS run contained in the file.  Each cycle contains
    an MS1 scan and the MS2 scans performed from that point, each of which has
    a different experiment number.  Note that as a result, all MS1 scans are 
    typically in experiment 1.    
    """
    
    def __init__(self, data_file, sample = 1, experiment = None, **etc):
        self.file_type = 'wiff'
        self.data_file = data_file
        
        try:
            self.source = CreateObject("{9eabbbb3-5a2a-4f73-aa60-f87b736d3476}")
        except WindowsError as err:
            print "WiffReaderCOM.dll not found in registry."
            raise err        
    
        if not os.path.exists(data_file + '.scan'):
            raise IOError, "%s.scan not found!" % data_file
    
        self.source.OpenWiffFile(os.path.abspath(data_file))
        
        self.sample = sample
            
        self.sample_count = self.source.GetSamples()
        self.experiment_count = self.source.GetExperiments(sample-1)
        
        # Will only store list for default parameters, 
        # since that's what's used by headers() and filters().
        self._scan_info = {} 
       
    
    def scan(self, scan_name, experiment, sample = None, centroid = False):
        """
        Retrieve a spectrum by the scan name.
        
        scan_name may either be the (int) cycle number, a (float) retention
        time value, a double (cycle number, experiment number) or a triple
        (cycle number, experiment number, sample number).
        
        If .set_sample and .set_experiment have not been called for a given mzFile
        instance, calls to .scan must specify these values.  Sample
        and experiment numbers given to .scan are not saved.
        """
        
        if sample == None:
            sample = self.sample            
        
        if centroid:
            raise NotImplementedError, 'Centroid argument to scan() does not currently work for WIFF files.'
                
        if isinstance(scan_name, int):
            cycle = scan_name  
        elif isinstance(scan_name, float):
            cycle = self.scan_for_time(scan_name, experiment, sample)
        else:
            raise NotImplementedError, "scan_name must be float or int."
     
        scan = self.source.GetSpectrumIndex(sample-1, experiment-1, cycle-1)
        return zip(*scan)
    
    
    def scan_info(self, start_cycle = 0, stop_cycle = 999999, experiment = None, sample = None):
        """
        Returns a list of [(time, mz, scan_name, scan_type, scan_mode)] in 
        the time and mz range provided in the sample and experiment specified
        (or all experiments and previously set sample, if not specified.)
        
        If sample is omitted, results are returned for the first sample.  If
        experiment is omitted, results are returned for all experiments
        in the sample.
        """
        
        if not sample:
            sample = self.sample
        
        if sample-1 in self._scan_info:
            return [x for x in self._scan_info[sample-1] if
                    start_cycle <= x[2][0] <= stop_cycle and
                    (experiment == None or x[2][1] == experiment)]
               
        # Zero- versus one-indexing gets dicy here.
        
        if sample:
            samples = [sample-1]
        else:
            raise Exception
        
        if experiment:
            expCounts = {sample-1 : [experiment]}
        else:
            expCounts = dict([(x, range(0, self.source.GetExperiments(x))) for x in samples])
        
        scaninfo = []
        cycleInfo = {}
        # samples is always length 1, due to the above checks, so this
        # is currently more complicated than it needs to be.
        for sample in samples:
            expInfo = {}
            for exp in expCounts[sample]:
                expInfo[exp] = self.source.ExperimentInfo(sample, exp)
                
            # Obnoxious to have to call this simply for percursor masses.
            cycleData = self.source.GetSampleData(sample) 
            for exp, cyc, rt, mass, cole in zip(*cycleData):
                cycleInfo[sample, int(exp), int(cyc)] = rt, mass, cole                
                
            cycles = self.source.GetNumCycles(sample)
            start_pt = max([0, start_cycle])
            stop_pt = min([cycles, stop_cycle])
            for cycle in range(start_pt, stop_pt):
                for exp in expCounts[sample]:
                    (level, precM, centroid, tof,
                     colE, minInt, maxInt) = self.source.GetScanData(sample, exp, cycle)
                    rt = self.source.GetRTOfScan(sample, exp, cycle)
                    
                    level = 'MS%d' % int(level)
                    precM = precM if precM > 0 else 0
                    centroid = 'p' if centroid > 0 else 'c'
                    
                    if (level != 'MS1') and not precM:
                        if debug:
                            pass
                            #assert not self.scan((cycle, exp, sample))
                        # It seems like this means it wasn't a real scan?
                        continue
                    
                    
                    scaninfo.append((rt, precM, (cycle+1, exp+1, sample+1), level, centroid))
                    
        
        if (start_cycle == 0 and stop_cycle == 999999 and
            experiment == None):
            self._scan_info[sample] = scaninfo
        
        return scaninfo
                    
                
                    
    def xic(self, start_time = 0, end_time = 999999, start_mz = 0, end_mz = 2000,
            sample = None, experiment = 1, filters = None):
        """
        Get the eXtracted Ion Chromatogram of the given time- and mz-range in
        the given sample data.  If sample is not specified, the previously set
        default (1, unless changed by set_sample()) is used.
        
        Experiment is by default 1 (the experiment of MS1 scans.)
        """
        if filters and filters.strip().lower() not in ['full ms', 'full ms2']:
            raise NotImplementedError, "Filter strings are not compatible with WIFF files. %s" % filters
        
        if not sample:
            sample = self.sample
        
        xic = zip(*self.source.XicByExp(sample-1, experiment-1, float(start_mz), float(end_mz)))
        return [x for x in xic if start_time <= x[0] <= end_time]
    
    
    def time_range(self, sample = None):
        """
        Gets the total retention time range of the sample.  By default uses the
        MS1 sample (1).
        """
        if sample == None:
            sample = self.sample
        
        return tuple(self.source.GetRTRange(sample-1))
    
    def scan_time_from_scan_name(self, cycle, experiment = None, sample = None):
        """
        Returns the retention time of a given cycle.
        """
        if sample == None:
            sample = self.sample
        
        return self.source.GetRTOfScan(sample-1, experiment-1, cycle-1)
    
    def scan_for_time(self, rt, experiment = None, sample = None):
        """
        Returns the cycle at a given retention time, if any.
        """        
        if sample == None:
            sample = self.sample
        
        return int(self.source.GetIndexOfRT(sample-1, experiment-1, rt))
    
    def scan_range(self, sample = None, experiment = None):
        """
        Returns the beginning and ending cycle numbers of the given sample.
        """
        if sample == None:
            sample = self.sample
            
        return (0, self.source.GetNumCycles(sample-1))
    
    
    def filters(self):
        """
        XCalibur-style MS filter strings, for back-cross-compatibility
        with scripts designed to work with raw.py.
        """
        
        expInfo = {}
        for sample in range(0, self.source.GetSamples()):
            for experiment in range(0, self.source.GetExperiments(sample)):
                expInfo[sample, experiment] = self.source.ExperimentInfo(sample, experiment)
        
        
        self._filters = []
        for rt, mz, (cycle, exp, sample), level, mode in self.scan_info():
            mzrange = map(int, expInfo[sample-1, exp-1][1:3]) # ...Perhaps?
            if level == 'MS1':
                levelstr = 'ms'
                locstr = ''
            elif level == 'MS2':
                levelstr = 'ms2'
                locstr = '%.2f@hcd00.00 ' % mz
            else:
                raise Exception, level
            
            filterstr = "FTMS + %s NSI Full %s %s[%d.00-%d.00]" % (mode, levelstr, locstr,
                                                           mzrange[0], mzrange[1])
            self._filters.append((rt, filterstr))
        
        return self._filters
        
    def headers(self, *etc):
        self._headers = self.scan_info()
        return self._headers
        
        

        
        
        
        
        

if __name__ == '__main__':
    print "TEST MODE"
    foo = mzFile_explicit_numbering(r'C:\Users\Max\Desktop\SpectrometerData\2015-05-27-CDK7-Indirect-Pep-4plex-3D-Exp0-16-100.WIFF')
    bar = foo.filters()
    #bar = foo.scan_info()
    
    #i = 0
    #j = 0
    #for _, precm, scanname, level, _ in bar:
        #if (not precm) and level != 'MS1':
            #i += 1
            #assert not foo.scan(scanname)
        #else:
            #if not foo.scan(scanname):
                #j += 1
                #print scanname
    
    
    #print i
    print "Done."
    



#if __name__ == '__main__':
    #from time import clock
    
    #foo = CreateObject("{9eabbbb3-5a2a-4f73-aa60-f87b736d3476}")
    #foo.OpenWiffFile(r'C:\Users\Max\Desktop\SpectrometerData\2015-05-27-CDK7-Indirect-Pep-4plex-3D-Exp0-16-100.WIFF')
    
    #time = clock()
    #for _ in range(0, 10):
        #start = clock()
        #foo.OpenWiffFile(r'C:\Users\Max\Desktop\SpectrometerData\2015-05-27-CDK7-Indirect-Pep-4plex-3D-Exp0-16-100.WIFF')
        #bar = foo.GetSpectrumIndex(0, 0, 1)
        #del foo
        #time += clock() - start
    
    #print time

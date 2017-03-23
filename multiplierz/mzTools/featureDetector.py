from multiplierz.mzAPI import mzFile
import matplotlib.pyplot as pyt
from collections import defaultdict, deque
import time
import multiplierz.mzReport as mzReport
import os
import numpy as np
import cPickle as pickle
import re
from multiplierz.internalAlgorithms import ProximityIndexedSequence, inPPM


from multiplierz.mzTools.featureUtilities import save_feature_database, FeatureInterface
from multiplierz.internalAlgorithms import peak_pick_PPM
import multiprocessing




__all__ = ['detectorRun']

# These (and the spectrumDescriptionTo... functions) will be modified when invoked by the GUI.
signalToNoiseThreshold = 15
#peakFindTolerance = 0.02

featureMatchupTolerance = 0.05

whitelist_tol = 0.1
def spectrumDescriptionToMZ(description):
    try:
        return float(description.split('-|')[0].split('-')[-1])
    except ValueError:
        return float(description.split("|")[1])

def spectrumDescriptionToScanNumber(description):
    return int(description.split('.')[1])


#allowedC12RatioDifference = 0.3
allowedC12RatioDifference = 2.0
#c12RelativeIntensity = 0.6
#c12RelativeLimit = 2
isotopicRatioTolerance = 2
noisePeaksAllowed = 1000000

#splitDueToIntensity = False
featureAbsenceTolerance = 10
dropoutTimeTolerance = 0.5

def unzip(thing): return [list(x) for x in zip(*thing)]

curveargs = [-1.02857097, 0.000113693166, 8.53554707]

def getC12Ratio(mz, charge):
    mass = mz * charge # Very approximately.
    return np.log(mass) * curveargs[0] + mass * curveargs[1] + curveargs[2]


class Feature():
    def __init__(self):
        self.regions = []
        self.spectrum = None
        self.scanrange = None
        self.allmzs = None
        self.wasSplit = False
    def add(self, datapoints, index, charge):
        self.regions.append((index, datapoints))
        self.charge = charge
    def allIndexedPoints(self):
        try:
            return sum([[(i, x, y, c, n) for (x, y, c, n) in region] for i, region in self.regions], [])
        except ValueError: # Not lscan-derived points.
            return sum([[(i, x, y) for (x, y) in region] for i, region in self.regions], [])
    def length(self):
        return len(self.regions)
    def strengthAt(self, index):
        try:
            return sum([x[1] for x in self.regions[index][1]])
        except IndexError:
            return 0
    def topSignal(self):
        return max([self.strengthAt(x) for x in range(0, self.length())])
    def totalIntensity(self):
        return sum([self.strengthAt(x) for x in range(0, self.length())])
    def c12Intensity(self):
        return sum([min(x, key = lambda x: x[0])[1] for _, x in self.regions])
    def segment(self, start, end):
        subFeature = Feature()
        subFeature.regions = self.regions[start:end]
        return subFeature
    def prepareBoxes(self, absScanLookup = None):
        if absScanLookup:
            if self.wasSplit: raise Exception, "Don't do this!"
        
            self.scans = [absScanLookup[x] for x, y in self.regions]
            
            minIndex = min([x for x, y in self.regions])
            try:
                minScan = absScanLookup[minIndex - 1]
            except KeyError:
                minScan = absScanLookup[minIndex]
            
            maxIndex = max([x for x, y in self.regions])
            try: 
                maxScan = absScanLookup[maxIndex + 1]
            except KeyError: 
                maxScan = absScanLookup[maxIndex]
        
            self.scanrange = minScan, maxScan
        else:
            assert self.scanrange

            minMZs = [min(x, key = lambda x: x[0])[0] for y, x in self.regions]
    
            #minMZRanks = [len([x for x in minMZs if abs(x - mz) < 0.1]) for mz in minMZs]
            ##self.mz = max(zip(minMZs, minMZRanks), key = lambda x: x[1])[0]
            #self.mz = max(zip(minMZRanks, minMZs), key = lambda x: (x[0], -1 * x[1]))[1]
            mz = np.average(minMZs)
            while not all([abs(x - mz) < 0.1 for x in minMZs]):
                lowhalf = [x for x in minMZs if x < mz]
                highhalf = [x for x in minMZs if x > mz]
                minMZs = max([lowhalf, highhalf], key = len)
                mz = np.average(minMZs)
            self.mz = mz
                
        
        #for i in range(0, len(self.regions)):
            #self.regions[i] = absScanLookup[self.regions[i][0]], self.regions[i][1]
    
    def containsPoint(self, mz, scan, charge):
        return (charge == self.charge and abs(mz - self.mz) < featureMatchupTolerance
                and self.scanrange[0] < scan < self.scanrange[1])
        
    def tidyData(self):
        self.allmzs = sum([[x[0] for x in points] for (index, points) in self.regions], [])
        
    def bordersPoint(self, mz, scan, charge):
        #if not self.scanrange:
            #self.scanrange = min(self.scans) - 1, max(self.scans) + 1
        if not self.allmzs:
            self.allmzs = sum([[x[0] for x in points] for (index, points) in self.regions], [])
        
        #assert not self.containsPoint(mz, scan, charge)
        if self.containsPoint(mz, scan, charge):
            return "Contained."
        
        if charge != self.charge:
            return ""
        
        edge = []
        
        if self.scanrange[0] == scan and abs(mz - self.mz) < 0.05:
            edge.append("Scan before feature")
        if self.scanrange[1] == scan and abs(mz - self.mz) < 0.05:
            edge.append("Scan after feature")
        if any([abs(x - mz) < 0.05 for x in self.allmzs]) and scan in self.scans:
            edge.append("Non-C12 peak")
        
        if edge:
            return '; '.join(edge)
        else:
            return ""
      
      
      
def setGlobals(constants):
    if 'mzRegex' in constants:
        global spectrumDescriptionToMZ
        
        mzRegCompiled = re.compile(constants['mzRegex'])
        
        def newParser(description):
            return float(mzRegCompiled.search(description).group())
        spectrumDescriptionToMZ = newParser
        
    if 'scanRegex' in constants:
        global spectrumDescriptionToScanNumber
        
        scanRegCompiled = re.compile(constants['scanRegex'])
        
        def newParser(description):
            return float(scanRegCompiled.search(description).group())
        spectrumDescriptionToScanNumber = newParser 
    
    #if 'featureTolerance' in constants:
        #global peakFindTolerance
        #peakFindTolerance = constants['featureTolerance']
    
    if 'signalNoiseThreshold' in constants:
        global signalToNoiseThreshold
        signalToNoiseThreshold = constants['signalNoiseThreshold']
        
    
    
    
    
def getAcqPoints(datafile, resultFile):
    data = mzFile(datafile)
    scans = data.scan_info(0, 999999)
    ms2toms1 = {}
    ms1 = scans[0][2]
    ms2s = []
    assert scans[0][3] == 'MS1'
    for scan in scans:
        if scan[3] == 'MS1':
            for ms2 in ms2s:
                ms2toms1[ms2] = ms1
            ms1 = scan[2]
            ms2s = []
        elif scan[3] == 'MS2':
            ms2s.append(scan[2])
        else:
            raise Exception, "Unidentified scan type of %s" % scan[3]
    for ms2 in ms2s:
        ms2toms1[ms2] = ms1
        
    acqPoints = []
    for result in resultFile:      
        mz = spectrumDescriptionToMZ(result['Spectrum Sescription'])
        scan = spectrumDescriptionToScanNumber(result['Spectrum Description'])
        scan = data.timeForScan(ms2toms1[scan])
        acqPoints.append((mz, scan))    
    
    return acqPoints
        
        

def binByFullFeature(datafile, featureDB, results):
    data = mzFile(datafile)
    
    scans = data.scan_info(0, 999999)
    ms2toms1 = {}
    ms1 = scans[0][2]
    ms2s = []
    assert scans[0][3] == 'MS1'
    for scan in scans:
        if scan[3] == 'MS1':
            for ms2 in ms2s:
                ms2toms1[ms2] = ms1
            ms1 = scan[2]
            ms2s = []
        elif scan[3] == 'MS2':
            ms2s.append(scan[2])
        else:
            raise Exception, "Unidentified scan type of %s" % scan[3]
    for ms2 in ms2s:
        ms2toms1[ms2] = ms1   
           
    matchesToSplits = 0
    matchesToUnsplit = 0
    featureItems = defaultdict(list)
    edgeItems = defaultdict(list)
    inexplicableItems = []
    for result in results:
        mz = spectrumDescriptionToMZ(result['Spectrum Description'])
        scan = spectrumDescriptionToScanNumber(result['Spectrum Description'])        
        charge = int(result['Charge'])
        scan = ms2toms1[scan]
        
        features = [(i, x) for i, x in featureDB.mz_range(mz - 0.01, mz + 0.01)
                    if x.containsPoint(mz, scan, charge)]
        if features:
            index, feature = min(features, key = lambda x: abs(x[1].mz - mz))
            scans = min(feature.scans), max(feature.scans)
            featureItems[index].append((result, scans))
        else:
            features = [(i, x) for i, x in featureDB.mz_range(mz - 1, mz + 1)
                        if x.bordersPoint(mz, scan, charge)]
            if features:
                index, feature = min(features, key = lambda x: abs(x[1].mz - mz))
                edge = feature.bordersPoint(mz, scan, charge)
                scans = min(feature.scans), max(feature.scans)
                edgeItems[index].append((result, edge, scans))
            else:
                inexplicableItems.append(result)
                
        
        
    groupedResults = []
    overFitCount = 0
    for feature, results in featureItems.items():
        pep = results[0][0]['Peptide Sequence']
        if not all([x['Peptide Sequence'] == pep for x, s in results]):
            #print feature
            overFitCount += 1
        
        for result, scans in results:
            result['Feature'] = feature
            result['feature error'] = '-'
            result['feature start scan'] = scans[0]
            result['feature end scan'] = scans[1]
            result['feature start time'] = data.timeForScan(scans[0])  if scans[0] else '-'
            result['feature end time'] = data.timeForScan(scans[1])  if scans[1] else '-'
            groupedResults.append(result)
    for feature, resultEdges in edgeItems.items():
        for result, edge, scans in resultEdges:
            result['Feature'] = '-'
            result['feature error'] = str(feature) + " " + edge
            result['feature start scan'] = scans[0]
            result['feature end scan'] = scans[1]
            result['feature start time'] = data.timeForScan(scans[0]) if scans[0] else '-'
            result['feature end time'] = data.timeForScan(scans[1]) if scans[1] else '-'          
            groupedResults.append(result)
    for result in inexplicableItems:
        result['Feature'] = '-'
        result['feature error'] = 'Feature not found'
        result['feature start scan'] = '-'
        result['feature end scan'] = '-'
        result['feature start time'] = '-'
        result['feature end time'] = '-'
        groupedResults.append(result)
            
    #print "Overfitting features: %s" % overFitCount
    #print "Split PSMs %s | Unsplit PSMs %s" % (matchesToSplits, matchesToUnsplit)

    data.close()
    return groupedResults



def falseCoverTest(datafile, searchResults, features):
    import numpy
    from scipy.optimize import curve_fit
    import random

    acqPoints = []
    data = mzFile(datafile)
    scans = data.scan_info(0, 999999)
    ms2toms1 = {}
    ms1 = scans[0][2]
    ms2s = []
    assert scans[0][3] == 'MS1'
    for scan in scans:
        if scan[3] == 'MS1':
            for ms2 in ms2s:
                ms2toms1[ms2] = ms1
                ms2toms1[ms1] = ms1
            ms1 = scan[2]
            ms2s = []
        elif scan[3] == 'MS2':
            ms2s.append(scan[2])
        else:
            raise Exception, "Unidentified scan type of %s" % scan[3]
    for ms2 in ms2s:
        ms2toms1[ms2] = ms1    
    
    for result in searchResults:
        mz = spectrumDescriptionToMZ(result['Spectrum Description'])
        scan = spectrumDescriptionToScanNumber(result['Spectrum Description'])           
        
        charge = int(result['Charge'])
        scan = ms2toms1[scan]
        acqPoints.append((mz, scan, charge))

    mzAcq, timeAcq, chargeAcq = unzip(acqPoints)
        
    def gauss(x, *p):
        A, mu, sigma = p
        return A*numpy.exp(-(x-mu)**2/(2.*sigma**2))
    
    def deriveGaussian(points):  
        initialP = [0.5, 0.5, 0.5]  

        hist, bin_edges = numpy.histogram(points)
        bin_centres = (bin_edges[:-1] + bin_edges[1:])/2
        
        coeff, var_mat = curve_fit(gauss, bin_centres.astype(np.float64), hist.astype(np.float64), p0=initialP)
        
        return coeff[1], coeff[2]
    
    rescale = 10000.0
    
    mzMu, mzSig = deriveGaussian(np.array(mzAcq) / rescale)
    timeMu, timeSig = deriveGaussian(np.array(timeAcq) / rescale)
    
    chargeCounts = defaultdict(int)
    for chg in chargeAcq:
        chargeCounts[chg] += 1
    total = 0
    for chg, count in chargeCounts.items():
        proportion = float(count) / float(len(chargeAcq))
        chargeCounts[chg] = proportion + total
        total += proportion
    chargeCounts = sorted(chargeCounts.items(), key = lambda x: x[1])
        
    def randomCharge():
        roll = random.random()
        return (x[0] for x in chargeCounts if roll < x[1]).next()
    
    def randomMS1():
        try:
            return ms2toms1[int(random.gauss(timeMu, timeSig) * rescale)]
        except KeyError:
            return randomMS1()
    
    randomPoints = []
    for _ in range(0, len(acqPoints)):
        mz = random.gauss(mzMu, mzSig) * rescale
        #time = ms2toms1[int(random.gauss(timeMu, timeSig) * rescale)]
        time = randomMS1()
        chg = randomCharge()
        randomPoints.append((mz, time, chg))     
        
        
    acqCoverage = 0
    randomCoverage = 0
    randomAdjacency = 0
    acqSet = set()
    randomSet = set()
    adjacentSet = set()
    for ptMZ, ptS, ptChg in acqPoints:
        for feature in features:
            if feature.containsPoint(ptMZ, ptS, ptChg):
                acqCoverage += 1
                acqSet.add(feature)
                break
    for ptMZ, ptS, ptChg in randomPoints:
        for feature in features:
            if feature.containsPoint(ptMZ, ptS, ptChg):
                randomCoverage += 1
                randomSet.add(feature)
                break
    for ptMZ, ptS, ptChg in randomPoints:
        for feature in features:
            if feature.bordersPoint(ptMZ, ptS, ptChg):
                randomAdjacency += 1
                adjacentSet.add(feature)
                break

    data.close()
    print "Valid %s, invalid %s, invalid edges %s" % (acqCoverage, randomCoverage, randomAdjacency)
    print "Completed."

        
        
  
  
def runSearch(datafile, resultFiles):
    assert datafile.lower().endswith('.raw'), "Only .raw files are currently supported."
  
    features = detectFeatures(datafile)  
  

def detectorRun(datafile, resultFiles,
                mzRegex = None, scanRegex = None,
                tolerance = None, signalNoise = None):
    
    """
    Performs feature-detection analysis on the given .RAW file and PSM
    reports. The output files group the given PSMs by feature, with the
    addition of source feature extent and intensity information.
    
    """


    import os

    if mzRegex:
        import re
        global spectrumDescriptionToMZ
        
        mzRegCompiled = re.compile(mzRegex)
        
        def newParser(description):
            return float(mzRegCompiled.search(description).group())
        spectrumDescriptionToMZ = newParser
    
    if scanRegex:
        import re
        global spectrumDescriptionToScanNumber
        
        scanRegCompiled = re.compile(scanRegex)
        
        def newParser(description):
            return int(scanRegCompiled.search(description).group())
        spectrumDescriptionToScanNumber = newParser
        
    #if tolerance:
        #global peakFindTolerance
        #peakFindTolerance = tolerance
    
    #if signalNoise:
        #global signalToNoiseThreshold
        #signalToNoiseThreshold = signalNoise
        
        


    assert os.path.exists(datafile), "%s not found!" % datafile
    for resultfile in resultFiles:
        assert os.path.exists(resultfile), "%s not found!" % resultfile
    assert datafile.lower().endswith('.raw'), "Only .raw files are currently supported."
    
    featureFile = detectFeatures(datafile)
    features = FeatureInterface(featureFile)
    
    
    if resultFiles:
        print resultFiles
        print "Categorizing search results by file."
        for resultfile in resultFiles:
            resultfile = os.path.abspath(resultfile)
            inputResults = mzReport.reader(resultfile)
            outputfile = '.'.join(resultfile.split('.')[:-1] + ['featureDetect', 'xlsx']) 
        
            
            
            resultsByFeature = binByFullFeature(datafile, features, inputResults)
            
            output = mzReport.writer(outputfile,
                                     columns = inputResults.columns + ['Feature',
                                                                       'feature error',
                                                                       'feature start scan',
                                                                       'feature end scan',
                                                                       'feature start time',
                                                                       'feature end time'])
            
            for result in resultsByFeature:
                output.write(result)
            
            output.close()
            
            print "Output saved to %s ." % outputfile
    else:
        print "No PSM data given; skipping annotation step."
        
    print "Done."




def dataReaderProc(datafile, que, scanNumbers):
    try:
        data = mzFile(datafile)
        
        for scanNum in scanNumbers:
            scan = data.scan(scanNum, centroid = True)
            que.put((scanNum, scan), block = True)
    
        que.put('done')
        data.close()
    except Exception as err:
        import traceback
        print "READ THREAD ERROR."
        traceback.print_exc()
        print '------------------'
        raise err

    


    
    
def detectFeatures(datafile, **constants):
    """
    Runs the feature detection algorithm on the target data file (currently,
    only Thermo .RAW is supported.)  Returns the path to the feature data
    file.
    
    Optional arguments:
    - tolerance (default 10): MZ tolerance in parts-per-million for all determinations
    of peak identity.  Should usually correspond to the mass precision of the
    source instrument.
    - force (default False): If True, feature detection is run even if a
    feature data file already exists for the target data.
    """
    
    
    featurefile = datafile + '.features'
    
    #constants = constants['constants']
    #if 'tolerance' in constants:
        #global tolerance
        #tolerance = constants['tolerance']
    #else:
        #tolerance = 0.01
    if 'tolerance' in constants:
        global tolerance
        tolerance = constants['tolerance']
        if tolerance < 1:
            print "\n\n\nWARNING- tolerance value for SILAC analysis should now be in PPM!\n\n\n"
    else:
        tolerance = 10
        
    if 'partial' in constants:
        # This is primarily for testing purposes only.
        scanrange = constants['partial']
    else:
        scanrange = None
        
    if 'force' in constants:
        force = constants['force']
    else:
        force = False
        
    if 'whitelist_psms' in constants:
        whitelist_mzs = constants['whitelist_psms']
        featurefile = datafile + '.partial%s.features' % (str(hash(frozenset(whitelist_mzs)))[:5])
    else:
        whitelist_mzs = None
        
    if 'peak_picking_params' in constants:
        peak_pick_params = constants['peak_picking_params']
    elif 'tolerance' in constants:
        peak_pick_params = {'tolerance':constants['tolerance']}
    else:
        peak_pick_params = {}
    
    if os.path.exists(featurefile) and not force:
        print "Feature data file already exists: %s" % featurefile
        return featurefile
    
    setGlobals(constants)

    
    times = []
    times.append(time.clock())
    data = mzFile(datafile)
    
    times.append(time.clock())
    print "Opened data file; getting isotopes..."

    scaninfo = [x for x in data.scan_info(0, 99999999) if x[3] == 'MS1']
    rtLookup = dict([(x[2], x[0]) for x in scaninfo])
    scaninfo = [x[2] for x in scaninfo]
    
    if scanrange:
        scaninfo = [x for x in scaninfo if scanrange[0] < x < scanrange[1]]

    data.close()
    
    que = multiprocessing.Queue(maxsize = 20)
    reader = multiprocessing.Process(target = dataReaderProc,
                                     args = (datafile, que, scaninfo))
    reader.start()
    
    isotopeData = deque()
    thing = que.get(block = True)
    bar = 0
    while thing != 'done':
        scanNum, scan = thing
        foo = time.clock()
        isotopeData.append((scanNum, peak_pick_PPM(scan, **peak_pick_params)[0]))
        bar += time.clock() - foo
        
        thing = que.get(block = True)
        
        if len(isotopeData) % 100 == 0:
            print len(isotopeData)
    
    reader.join()
    # Could just discard the un-feature'd peaks immediately.
    print "Isotopic features acquired; finding features over time..."

    times.append(time.clock())

    ms1ToIndex = {}
    indexToMS1 = {}
    for index, scanNum in enumerate(scaninfo):
        ms1ToIndex[scanNum] = index
        indexToMS1[index] = scanNum

            
    isotopesByChargePoint = defaultdict(lambda: defaultdict(lambda: ProximityIndexedSequence([], lambda x: x[0][0])))
    allIsotopes = []
    for scanNum, isotopesByCharge in isotopeData:
        scanIndex = ms1ToIndex[scanNum]
        for charge, isotopes in isotopesByCharge.items():
            for isoSeq in isotopes:
                #isotopesByChargePoint[charge][scanIndex].append(isoSeq)
                isotopesByChargePoint[charge][scanIndex].add(isoSeq)
                allIsotopes.append((isoSeq, scanIndex, charge))
    
    del isotopeData

    for scanlookup in isotopesByChargePoint.values():
        for proxseq in scanlookup.values():
            proxseq.rebalance()
            

    if whitelist_mzs:
        print "Screening out irrelevant MZs; starting with %s..." % len(allIsotopes)
        allIsotopes.sort(key = lambda x: x[0][0][0])
        whitelist_mzs = sorted(list(set([round(x, 2) for x in whitelist_mzs])))
        isoAcc = []
        whitemz = whitelist_mzs.pop()
        while allIsotopes:
            iso = allIsotopes.pop()
            mz = iso[0][0][0]
            while whitelist_mzs and whitemz - mz > whitelist_tol:
                whitemz = whitelist_mzs.pop()
            if abs(whitemz - mz) < whitelist_tol:
                isoAcc.append(iso)
        
        allIsotopes = isoAcc
        print "...%s remain." % len(allIsotopes)
    
    
    
    allIsotopes.sort(key = lambda x: x[0][0][1])
    

    
    times.append(time.clock())    
    
    seenIsotopes = set()
    # Can assume isotopic sequences are unique because floats.
    # (But it may not be a valid assumption, because detectors
    # and floating point approximations!)
    
    featureList = []
    print "Entering loop!"
    while allIsotopes:
        highIso, highScan, highChg = allIsotopes.pop()
        if tuple(highIso) in seenIsotopes:
            continue
        
        centerIndex, (centerMZ, _) = max(enumerate(highIso), 
                                         key = lambda x: x[1][1])
        
        newFeature = [[highScan, highIso]]
        curScan = highScan
        continuing = True
        lastSeen = rtLookup[indexToMS1[curScan]]
        while continuing: # Trailing the feature backwards.
            curScan -= 1
            try:
                curRT = rtLookup[indexToMS1[curScan]]
            except KeyError:
                assert curScan < max(indexToMS1.keys())
                break
            #continuing = False
            found = False
            
            #scanSeqs = [iso for iso in isotopesByChargePoint[highChg][curScan]
                        #if any([abs(x[0] - centerMZ) < tolerance for x in iso])]
            scanSeqs = isotopesByChargePoint[highChg][curScan].returnRange(centerMZ - 2, centerMZ + 1.5)
            scanSeqs.sort(key = lambda x: x[centerIndex][1], reverse = True)
            
            for iso in scanSeqs: # These are known to have centerMZ in common.
                # The indexes between iso and highIso may not be equivalent
                # if there's sub-C12 peak(s) in either.  For a first draft
                # this can be considered a feature, since C12s should be
                # consistent throughout features, but in some cases like
                # single-scan-dropouts of the C12 this is insufficient
                # and such discrepancies should be accounted for.
                
                #if (abs(iso[0][0] - highIso[0][0]) < tolerance
                    #and abs(iso[1][0] - highIso[1][0]) < tolerance
                if (inPPM(tolerance, iso[0][0], highIso[0][0])
                    and inPPM(tolerance, iso[1][0], highIso[1][0])
                    and tuple(iso) not in seenIsotopes):
                    newFeature.append([curScan, iso])
                    found = True
                    break # From "for iso in scanSeqs"                    
            
            if found:
                lastSeen = curRT
            elif abs(curRT - lastSeen) > dropoutTimeTolerance:
                continuing = False
        
        curScan = highScan
        continuing = True
        lastSeen = rtLookup[indexToMS1[curScan]]
        while continuing: # Trailing the feature forwards; mostly repeat code.
            curScan += 1
            try:
                curRT = rtLookup[indexToMS1[curScan]]
            except KeyError:
                assert curScan > max(indexToMS1.keys())
                break
            found = False
            
            #scanSeqs = [iso for iso in isotopesByChargePoint[highChg][curScan]
                        #if any([abs(x[0] - centerMZ) < tolerance for x in iso])]
            scanSeqs = isotopesByChargePoint[highChg][curScan].returnRange(centerMZ - 2, centerMZ + 1.5)
            scanSeqs.sort(key = lambda x: x[centerIndex][1], reverse = True)

            for iso in scanSeqs: # These are known to have centerMZ in common.
                # Ditto.
                
                #if (abs(iso[0][0] - highIso[0][0]) < tolerance
                    #and abs(iso[1][0] - highIso[1][0]) < tolerance                
                if (inPPM(tolerance, iso[0][0], highIso[0][0])
                    and inPPM(tolerance, iso[1][0], highIso[1][0])
                    and tuple(iso) not in seenIsotopes):
                    newFeature.append([curScan, iso])
                    found = True
                    break # From "for iso in scanSeqs"                    

            if found:
                lastSeen = curRT
            elif abs(curRT - lastSeen) > dropoutTimeTolerance:
                continuing = False

        if len(newFeature) > 1:
            featureList.append((highChg, newFeature))
        
        for _, iso in newFeature:
            #assert tuple(iso) not in seenIsotopes
            seenIsotopes.add(tuple(iso))
    
    print "Exited loop!"
    times.append(time.clock())
    
    for chg, feature in featureList:
        for stage in feature:
            stage[0] = indexToMS1[stage[0]]
            
    print "A %s" % time.clock()
    class idLookup():
        def __getitem__(self, thing):
            return thing
    lookup = idLookup()

    print "B %s" % time.clock()
    if scanrange:
        featurefile = datafile + ('%s-%s.features' % scanrange)

    print "C %s" % time.clock()
    featureObjects = []
    for chg, feature in featureList:
        newfeature = Feature()
        for scan, envelope in feature:
            newfeature.add(envelope, scan, chg)
        
        newfeature.prepareBoxes(lookup)
        newfeature.prepareBoxes() # It's entirely different, for some reason?
        featureObjects.append(newfeature)
    print "D %s" % time.clock()
    save_feature_database(featureObjects, featurefile)
    
    print "Saved feature file."
    times.append(time.clock())
    
    print times
    return featurefile





    
    
    
# RUNNING FEATURE DETECTION BY USING THIS FILE AS __MAIN__ DOESN'T WORK
#!bin/python
"""
Graph consists of:
  input/* (read at startup)
  webinput/* (new files are noticed in here)
  any number of remote graphs, specified in the other graph as objects of (:reasoning, :source, *), reread constantly

gather subgraphs from various services, run them through a rules
engine, and make http requests with the conclusions.

E.g. 'when drew's phone is near the house, and someone is awake,
unlock the door when the door's motion sensor is activated'

When do we gather? The services should be able to trigger us, perhaps
with PSHB, that their graph has changed.
"""


from twisted.internet import reactor, task
from twisted.web.client import getPage
from twisted.python.filepath import FilePath
import time, traceback, sys, json, logging
from rdflib.Graph import Graph, ConjunctiveGraph
from rdflib import Namespace, URIRef, Literal, RDF, StringInputSource
import restkit
from FuXi.Rete.RuleStore import N3RuleStore
import cyclone.web
from inference import addTrig, infer
from graphop import graphEqual

sys.path.append("../../lib")
from logsetup import log
log.setLevel(logging.DEBUG)

ROOM = Namespace("http://projects.bigasterisk.com/room/")
DEV = Namespace("http://projects.bigasterisk.com/device/")


class InputGraph(object):
    def __init__(self, inputDirs, onChange):
        """
        all .n3 files from inputDirs will be read.

        onChange(self) is called if the contents of the full graph change
        (in an interesting way) during updateFileData or
        updateRemoteData. Interesting means statements other than the
        ones with the predicates on the boring list.
        """
        self.inputDirs = inputDirs
        self.onChange = onChange
        self._fileGraph = Graph()
        self._remoteGraph = None
        self._combinedGraph = None
        self._oneShotAdditionGraph = None

    def updateFileData(self):
        """
        make sure we contain the correct data from the files in inputDirs
        """
        # this sample one is actually only needed for the output, but I don't
        # think I want to have a separate graph for the output
        # handling
        log.debug("read file graphs")
        for fp in FilePath("input").walk():
            if fp.isdir():
                continue
            log.debug("read %s", fp)
            # todo: if this fails, leave the report in the graph
            self._fileGraph.parse(fp.open(), format="n3")
            self._combinedGraph = None

        self.onChange(self)

    def updateRemoteData(self):
        """
        read all remote graphs (which are themselves enumerated within
        the file data)
        """
        log.debug("read remote graphs")
        g = ConjunctiveGraph()
        for source in self._fileGraph.objects(ROOM['reasoning'],
                                              ROOM['source']):
            try:
                # this part could be parallelized
                fetchTime = addTrig(g, source)
            except Exception, e:
                log.error("  adding source %s: %s", source, e)
                g.add((URIRef(source), ROOM['graphLoadError'], Literal(str(e))))
                g.add((URIRef(source), RDF.type, ROOM['FailedGraphLoad']))
            else:
                g.add((URIRef(source), ROOM['graphLoadSecs'],
                       Literal(fetchTime)))
        prevGraph = self._remoteGraph
        self._remoteGraph = g
        self._combinedGraph = None
        if (prevGraph is None or
            not graphEqual(g, prevGraph, ignorePredicates=[
                ROOM.signalStrength,
                ROOM.graphLoadSecs,
                # perhaps anything with a number-datatype for its
                # object should be filtered out, and you have to make
                # an upstream quantization (e.g. 'temp high'/'temp
                # low') if you want to do reasoning on the difference
                URIRef("http://bigasterisk.com/map#lastSeenAgoSec"),
                URIRef("http://bigasterisk.com/map#lastSeenAgo"),
                URIRef("http://projects.bigasterisk.com/room/usingPower"),
                ])):
            log.debug("  remote graph changed")
            self.onChange(self)
        else:
            log.debug("  remote graph is unchanged")

    def addOneShot(self, g):
        """
        add this graph to the total, call onChange, and then revert
        the addition of this graph
        """
        self._oneShotAdditionGraph = g
        try:
            self.onChange(self)
        finally:
            self._oneShotAdditionGraph = None

    def getGraph(self):
        """rdflib Graph with the file+remote contents of the input graph"""
        # this could be much faster with the combined readonly graph
        # view from rdflib
        if self._combinedGraph is None:
            self._combinedGraph = Graph()
            if self._fileGraph:
                for s in self._fileGraph:
                    self._combinedGraph.add(s)
            if self._remoteGraph:
                for s in self._remoteGraph:
                    self._combinedGraph.add(s)
            if self._oneShotAdditionGraph:
                for s in self._oneShotAdditionGraph:
                    self._combinedGraph.add(s)                

        return self._combinedGraph 

class Reasoning(object):
    def __init__(self):
        self.prevGraph = None
        self.lastPollTime = 0
        self.lastError = ""

        self.rulesN3 = "(not read yet)"
        self.inferred = Graph() # gets replaced in each graphChanged call

        self.inputGraph = InputGraph([], self.graphChanged)
        self.inputGraph.updateFileData()


    def readRules(self):
        self.rulesN3 = open('rules.n3').read() # for web display
        self.ruleStore = N3RuleStore()
        self.ruleGraph = Graph(self.ruleStore)
        self.ruleGraph.parse('rules.n3', format='n3') # for inference

    def poll(self):
        try:
            self.inputGraph.updateRemoteData()
            self.lastPollTime = time.time()
        except Exception, e:
            log.error(traceback.format_exc())
            self.lastError = str(e)

    def graphChanged(self, inputGraph):
        # i guess these are getting consumed each inference
        try:
            t1 = time.time()
            self.readRules()
            ruleParseTime = time.time() - t1
        except ValueError, e:
            # this is so if you're just watching the inferred output,
            # you'll see the error too
            self.inferred = Graph()
            self.inferred.add((ROOM['reasoner'], ROOM['ruleParseError'],
                               Literal(traceback.format_exc())))
            raise

        g = inputGraph.getGraph()
        t1 = time.time()
        self.inferred = infer(g, self.ruleStore)
        inferenceTime = time.time() - t1

        self.inferred.add((ROOM['reasoner'], ROOM['ruleParseTime'],
                           Literal(ruleParseTime)))
        self.inferred.add((ROOM['reasoner'], ROOM['inferenceTime'],
                           Literal(inferenceTime)))

        self.putResults(self.inferred)

        try:
            inputGraphNt = g.serialize(format="nt")
            inferredNt = self.inferred.serialize(format="nt")
            body = json.dumps({"input": inputGraphNt,
                               "inferred": inferredNt})
            restkit.Resource("http://bang:8014/").post(
                "reasoningChange", payload=body,
                headers={"content-type" : "application/json"})
        except Exception, e:
            traceback.print_exc()
            log.error("while sending changes to magma:")
            log.error(e)


    def putResults(self, inferred):
        """
        some conclusions in the inferred graph lead to PUT requests
        getting made

        if the graph contains (?d ?p ?o) and ?d and ?p are a device
        and predicate we support PUTs for, then we look up
        (?d :putUrl ?url) and (?o :putValue ?val) and call
        PUT ?url <- ?val

        If the graph doesn't contain any matches, we use (?d
        :zeroValue ?val) for the value and PUT that.
        """
        deviceGraph = self.inputGraph.getGraph()
        for dev, pred in [
            # the config of each putUrl should actually be in the
            # context of a dev and predicate pair, and then that would
            # be the source of this list
            (DEV.theaterDoorLock, ROOM.state),
            (URIRef('http://bigasterisk.com/host/bang/monitor'), ROOM.powerState),
            (URIRef('http://bigasterisk.com/host/dash/monitor'), ROOM.powerState),
            ]:
            url = deviceGraph.value(dev, ROOM.putUrl)

            if dev == DEV.theaterDoorLock: # ew
                restkit.request(url=url+"/mode", method="PUT", body="output")

            inferredObjects = list(inferred.objects(dev, pred))
            if len(inferredObjects) == 0:
                self.putZero(deviceGraph, dev, pred, url)
            elif len(inferredObjects) == 1:
                self.putInferred(deviceGraph, dev, pred, url, inferredObjects[0])
            elif len(inferredObjects) > 1:
                log.info("conflict, ignoring: %s has %s of %s" %
                         (dev, pred, inferredObjects))
                # write about it to the inferred graph?

        self.frontDoorPuts(deviceGraph, inferred)

    def putZero(self, deviceGraph, dev, pred, putUrl):
        # zerovalue should be a function of pred as well.
        value = deviceGraph.value(dev, ROOM.zeroValue)
        if value is not None:
            log.info("put zero (%r) to %s", value, putUrl)
            restkit.request(url=putUrl, method="PUT", body=value)
            # this should be written back into the inferred graph
            # for feedback

    def putInferred(self, deviceGraph, dev, pred, putUrl, obj):
        value = deviceGraph.value(obj, ROOM.putValue)
        if value is not None:
            log.info("put %s to %s", value, putUrl)
            restkit.request(url=putUrl, method="PUT", body=value)
        else:
            log.warn("%s %s %s has no :putValue" %
                     (dev, pred, obj))

    def frontDoorPuts(self, deviceGraph, inferred):
        # todo: shouldn't have to be a special case
        brt = inferred.value(DEV.frontDoorLcd, ROOM.brightness)
        if brt is None:
            return
        url = deviceGraph.value(DEV.frontDoorLcdBrightness, ROOM.putUrl)
        log.info("put lcd %s brightness %s", url, brt)
        def failed(err):
            log.error("lcd brightness: %s", err)
        getPage(str(url) + "?brightness=%s" % str(brt),
                method="PUT").addErrback(failed)

        msg = "open %s motion %s" % (
            inferred.value(DEV['frontDoorOpenIndicator'], ROOM.text),
            inferred.value(DEV['frontDoorMotionIndicator'], ROOM.text))
        # this was meant to be 2 chars in the bottom row, but the
        # easier test was to replace the whole top msg
        #restkit.Resource("http://slash:9080/").put("lcd", message=msg)



class Index(cyclone.web.RequestHandler):
    def get(self):
        # make sure GET / fails if our poll loop died
        ago = time.time() - self.settings.reasoning.lastPollTime
        if ago > 2:
            self.set_status(500)
            self.finish("last poll was %s sec ago. last error: %s" %
                        (ago, self.settings.reasoning.lastError))
            return
        self.set_header("Content-Type", "application/xhtml+xml")
        self.write(open('index.html').read())

class ImmediateUpdate(cyclone.web.RequestHandler):
    def put(self):
        """
        request an immediate load of the remote graphs; the thing we
        do in the background anyway. No payload.

        Using PUT because this is idempotent and retryable and
        everything.
        """
        r.poll()
        self.set_status(202)

def parseRdf(text, contentType):
    g = Graph()
    g.parse(StringInputSource(text), format={
        'text/n3': 'n3',
        }[contentType])
    return g

class OneShot(cyclone.web.RequestHandler):
    def post(self):
        """
        payload is an rdf graph. The statements are momentarily added
        to the input graph for exactly one update.

        todo: how do we go from a transition like doorclosed-to-open
        to a oneshot event? the upstream shouldn't have to do it. Do
        we make those oneshot events here? for every object change?
        there are probably special cases regarding startup time when
        everything appears to be a 'change'.
        """
        g = parseRdf(self.request.body, self.request.headers['content-type'])
        self.settings.reasoning.inputGraph.addOneShot(g)

# for reuse
class GraphResource(cyclone.web.RequestHandler):
    def get(self, which):
        self.set_header("Content-Type", "application/json")
        r = self.settings.reasoning
        g = {'lastInput': r.inputGraph.getGraph(),
             'lastOutput': r.inferred,
             }[which]
        self.write(self.jsonRdf(g))

    def jsonRdf(self, g):
        return json.dumps(sorted(list(g)))

class NtGraphs(cyclone.web.RequestHandler):
    """same as what gets posted above"""
    def get(self):
        r = self.settings.reasoning
        inputGraphNt = r.inputGraph.getGraph().serialize(format="nt")
        inferredNt = r.inferred.serialize(format="nt")
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"input": inputGraphNt,
                               "inferred": inferredNt}))

class Rules(cyclone.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/plain")
        self.write(self.settings.reasoning.rulesN3)

class Status(cyclone.web.RequestHandler):
    def get(self):
        self.set_header("Content-Type", "text/plain")
        g = self.settings.reasoning.inputGraph.getGraph()
        msg = ""
        for badSource in g.subjects(RDF.type, ROOM['FailedGraphLoad']):
            msg += "GET %s failed (%s). " % (
                badSource, g.value(badSource, ROOM['graphLoadError']))
        if not msg:
            self.finish("all inputs ok")
            return
        self.set_status(500)
        self.finish(msg)

class Static(cyclone.web.RequestHandler):
    def get(self, p):
        self.write(open(p).read())

class Application(cyclone.web.Application):
    def __init__(self, reasoning):
        handlers = [
            (r"/", Index),
            (r"/immediateUpdate", ImmediateUpdate),
            (r"/oneShot", OneShot),
            (r'/(jquery.min.js)', Static),
            (r'/(lastInput|lastOutput)Graph', GraphResource),
            (r'/ntGraphs', NtGraphs),
            (r'/rules', Rules),
            (r'/status', Status),
        ]
        cyclone.web.Application.__init__(self, handlers, reasoning=reasoning)

if __name__ == '__main__':
    r = Reasoning()
    #import twisted.python.log
    #twisted.python.log.startLogging(sys.stdout)

    task.LoopingCall(r.poll).start(1.0)
    reactor.listenTCP(9071, Application(r))
    reactor.run()

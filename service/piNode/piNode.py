from __future__ import division
import sys, logging, socket, json
import cyclone.web
from rdflib import Namespace, URIRef, Literal, Graph, RDF
from rdflib.parser import StringInputSource
from twisted.internet import reactor, task
from docopt import docopt
logging.basicConfig(level=logging.DEBUG)
sys.path.append("/my/site/magma")
sys.path.append("../../../../site/magma")

from stategraph import StateGraph
sys.path.append('/home/pi/dim/PIGPIO')
try:
    import pigpio
except ImportError:
    class pigpio(object):
        @staticmethod
        def pi():
            return None

import devices

log = logging.getLogger()
logging.getLogger('serial').setLevel(logging.WARN)
ROOM = Namespace('http://projects.bigasterisk.com/room/')
HOST = Namespace('http://bigasterisk.com/ruler/host/')

hostname = socket.gethostname()

class Config(object):
    def __init__(self):
        self.graph = Graph()
        log.info('read config')
        self.graph.parse('config.n3', format='n3')
        self.graph.bind('', ROOM) # not working
        self.graph.bind('rdf', RDF)

class GraphPage(cyclone.web.RequestHandler):
    def get(self):
        g = StateGraph(ctx=ROOM['pi/%s' % hostname])

        for stmt in self.settings.board.currentGraph():
            g.add(stmt)

        if self.get_argument('config', 'no') == 'yes':
            for stmt in self.settings.config.graph:
                g.add(stmt)
        
        self.set_header('Content-type', 'application/x-trig')
        self.write(g.asTrig())

class Board(object):
    """similar to arduinoNode.Board but without the communications stuff"""
    def __init__(self, graph, uri, onChange):
        self.graph, self.uri = graph, uri
        self.pi = pigpio.pi()
        self._devs = devices.makeDevices(graph, self.uri, self.pi)
        log.debug('found %s devices', len(self._devs))
        self._statementsFromInputs = {} # input device uri: latest statements

    def startPolling(self):
        task.LoopingCall(self._poll).start(.5)

    def _poll(self):
        for i in self._devs:
            self._statementsFromInputs[i.uri] = i.poll()
        
    def outputStatements(self, stmts):
        unused = set(stmts)
        for dev in self._devs:
            stmtsForDev = []
            for pat in dev.outputPatterns():
                if [term is None for term in pat] != [False, False, True]:
                    raise NotImplementedError
                for stmt in stmts:
                    if stmt[:2] == pat[:2]:
                        stmtsForDev.append(stmt)
                        unused.discard(stmt)
            if stmtsForDev:
                log.info("output goes to action handler for %s" % dev.uri)
                dev.sendOutput(stmtsForDev)
                log.info("success")
        if unused:
            log.warn("No devices cared about these statements:")
            for s in unused:
                log.warn(repr(s))
        
    def currentGraph(self):
        g = Graph()
        
        g.add((HOST[socket.gethostname()], ROOM['connectedTo'], self.uri))

        for si in self._statementsFromInputs.values():
            for s in si:
                g.add(s)
        return g

    def description(self):
        """for web page"""
        return {
            'uri': self.uri,
            'devices': [d.description() for d in self._devs],
            'graph': 'http://sticker:9059/graph', #todo
            }
        
def rdfGraphBody(body, headers):
    g = Graph()
    g.parse(StringInputSource(body), format='nt')
    return g

class OutputPage(cyclone.web.RequestHandler):
    def put(self):
        arg = self.request.arguments
        if arg.get('s') and arg.get('p'):
            subj = URIRef(arg['s'][-1])
            pred = URIRef(arg['p'][-1])
            turtleLiteral = self.request.body
            try:
                obj = Literal(float(turtleLiteral))
            except ValueError:
                obj = Literal(turtleLiteral)
            stmt = (subj, pred, obj)
        else:
            g = rdfGraphBody(self.request.body, self.request.headers)
            assert len(g) == 1, len(g)
            stmt = g.triples((None, None, None)).next()

        self.settings.board.outputStatements([stmt])

class Boards(cyclone.web.RequestHandler):
    def get(self):
        self.set_header('Content-type', 'application/json')
        self.write(json.dumps({
            'boards': [self.settings.board.description()]
        }, indent=2))
        
def main():
    arg = docopt("""
    Usage: piNode.py [options]

    -v   Verbose
    """)
    log.setLevel(logging.WARN)
    if arg['-v']:
        from twisted.python import log as twlog
        twlog.startLogging(sys.stdout)

        log.setLevel(logging.DEBUG)
    
    config = Config()

    def onChange():
        # notify reasoning
        pass

    thisBoard = URIRef('http://bigasterisk.com/homeauto/node2')
    
    board = Board(config.graph, thisBoard, onChange)
    board.startPolling()
    
    reactor.listenTCP(9059, cyclone.web.Application([
        (r"/()", cyclone.web.StaticFileHandler, {
            "path": "../arduinoNode/static", "default_filename": "index.html"}),
        (r'/static/(.*)', cyclone.web.StaticFileHandler, {"path": "../arduinoNode/static"}),
        (r"/graph", GraphPage),
        (r'/output', OutputPage),
        (r'/boards', Boards),
        #(r'/dot', Dot),
        ], config=config, board=board, debug=arg['-v']))
    reactor.run()

main()
import re, time
import restkit
from rdflib import URIRef
try:
    from rdflib import StringInputSource
    from rdflib.Graph import Graph
except ImportError:
    from rdflib.parser import StringInputSource
    from rdflib import Graph

def parseTrig(trig):
    """
    yields quads
    """
    m = re.match(r"<([^>]+)> \{(.*)\}\s*$", trig, re.DOTALL)
    if m is None:
        raise NotImplementedError("trig format was too tricky: %r..." % trig[:200])
        
    ctx = URIRef(m.group(1))
    n3 = m.group(2)
    g = Graph()
    g.parse(StringInputSource(n3), format="n3")
    for stmt in g:
        yield stmt + (ctx,)

        
def addTrig(graph, url, timeout=2):
    t1 = time.time()
    response = restkit.request(url, timeout=timeout)
    if response.status_int != 200:
        raise ValueError("status %s from %s" % (response.status, url))
    trig = response.body_string()
    fetchTime = time.time() - t1
    graph.addN(parseTrig(trig))
    return fetchTime
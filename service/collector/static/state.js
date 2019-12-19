import { LitElement, TemplateResult, html, css } from '/lib/lit-element/2.2.0/lit-element-custom.js';

class CollectorState extends LitElement {
    static get properties() {
        return {
            latestState: { type: Object }
        };
    }
    firstUpdated() {
        this.latestState = {graphClients: {}};
        this.refreshLoop();
    }

    refreshLoop() {
        setTimeout(() => {
            requestAnimationFrame(() => {
                this.refresh();
            });
        }, 5000);
    }

    refresh() {
        fetch('state')
            .then((response) => {
                return response.json();
            })
            .then((newState) => {
                this.latestState = newState;
                this.refreshLoop();
            });
    }

    static get styles() {
        return css`
        :host {
          display: inline-block;
          border: 2px solid gray;
          padding: 5px;
        }`;
    }

    render() {
        const sourcesTable = (clients) => {
            const clientRow = (client) => {
                const d = client.reconnectedPatchSource;
                const now = Date.now() / 1000;
                const dispSec = (sec) => (
                    Math.abs(sec) > now - 1 ? '--' :
                        Math.abs(sec) > 3600 ?
                        `${Math.round(sec/3600)} hr` : Math.abs(sec) > 60 ?
                        `${Math.round(sec/60)} min` :
                        `${Math.round(sec*10)/10} sec`);
                return html`
                  <tr>
                    <td><a href="/rdf/browse/?graph=${d.url}">[browse]</a> <a href="${d.url}">${d.url}</a></td>
                    <td>${d.fullGraphReceived}</td>
                    <td>${d.patchesReceived}</td>
                    <td>${dispSec(d.time.open - now)}</td>
                    <td>${dispSec(d.time.fullGraph - d.time.open)}</td>
                    <td>${dispSec(d.time.latestPatch - now)}</td>
                  </tr>
                `;
            };

            return html`
              <table>
                <thead>
                  <tr>
                    <th>patch source</th>
                    <th>full graph recv</th>
                    <th>patches recv</th>
                    <th>time open (rel)</th>
                    <th>time fullGraph (after open)</th>
                    <th>time latest patch (rel)</th>
                  </tr>
                </thead>
                <tbody>
                  ${clients.map(clientRow)}
                </tbody>
              </table>
              `;
        };

        const handlersTable = (handlers) => {
            const handlerRow = (d) => {
                return html`
                  <tr>
                    <td>${d.created}</td>
                    <td>${d.ageHours}</td>
                    <td><a href="/rdf/browse/?graph=/sse_collector/graph/${d.streamId}">${d.streamId}</a></td>
                    <td>${d.foafAgent}</td>
                    <td>${d.userAgent}</td>
                  </tr>
                  `;
            };

            return html`
              <table>
                <thead>
                  <tr>
                    <th>created</th>
                    <th>age hours</th>
                    <th>stream</th>
                    <th>foaf agent</th>
                    <th>user agent</th>
                  </tr>
                </thead>
                <tbody>
                  ${handlers.map(handlerRow)}
                </tbody>
              </table>
              `;
        };

        if (!this.latestState) {
            return 'loading...';
        }
        const d = this.latestState.graphClients;
        return html`
          <div>
            <p>
              Graph: ${d.statements ? d.statements.len : 0} statements
            </p>

            <p>
              Sources:
              ${sourcesTable(d.clients || [])}
            </p>

            <p>
              Listening clients:
              ${handlersTable(d.sseHandlers || [])}
            </p>
          </div>`;
    }
}
customElements.define('collector-state', CollectorState);

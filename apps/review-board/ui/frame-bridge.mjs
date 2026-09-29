// Fred's host owns team routing and bearer tokens. No direct service fetch here.
//
// The transport is Fred's published client; this class is the thin layer around
// it that the dashboard speaks to. It keeps two things the client has no opinion
// about: a generation counter, so work started for one team cannot land after a
// switch to another, and the error wording the interface shows.
import { createFredApplicationClient } from "./fred-iframe-sdk.js";

const MAX_IN_FLIGHT = 8;

export class FrameBridge {
  constructor({ origin, onContext, timeout = 60000, applicationId = "review-board" }) {
    this.onContext = onContext;
    this.generation = 0;
    this.inFlight = 0;
    this.teamId = null;
    this.client = createFredApplicationClient({
      hostOrigin: origin,
      applicationId,
      connectionTimeoutMs: timeout,
      requestTimeoutMs: timeout,
    });
  }

  // Bumping the generation is what makes a response for the previous team
  // unusable: every in-flight request captured the old value and is checked
  // against the current one before it resolves.
  clear() {
    this.generation += 1;
  }

  #acceptContext(context) {
    const team = context?.team;
    const nextTeam =
      typeof team?.id === "string" && !team.isPersonal ? team.id : null;
    const changed = nextTeam !== this.teamId;
    if (changed) this.clear();
    this.teamId = nextTeam;
    this.onContext(context, changed);
  }

  async connect() {
    // Subscribing is itself an action on a connected client, so it cannot come
    // first: the handshake has to resolve before the listener can be attached.
    const context = await this.client.connect();
    this.client.onContext((next) => this.#acceptContext(next));
    this.#acceptContext(context);
    return context;
  }

  openChat(sessionId = null) {
    this.client.openChat(sessionId);
  }

  dispose() {
    this.clear();
    this.client.dispose();
  }

  async request(path, { method = "GET", body = null } = {}) {
    if (!this.teamId)
      throw new Error("Select a collaborative team in Fred.");
    if (this.inFlight >= MAX_IN_FLIGHT)
      throw new Error("Too many requests; please try again.");
    const generation = this.generation;
    this.inFlight += 1;
    let response;
    try {
      response = await this.client.request(path, {
        method,
        headers: body ? { "content-type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : null,
      });
    } catch (cause) {
      if (generation !== this.generation) throw new Error("Team changed");
      throw new Error(
        cause?.code === "request-timeout"
          ? "The application did not respond. Please refresh."
          : "Fred could not reach the application service.",
      );
    } finally {
      this.inFlight -= 1;
    }
    if (generation !== this.generation) throw new Error("Team changed");

    const text = await response.text();
    let parsed;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      throw new Error("The application returned an invalid response.");
    }
    if (response.ok) return parsed;
    const detail =
      typeof parsed?.detail === "string"
        ? parsed.detail
        : "The request was not accepted.";
    const error = new Error(`HTTP ${response.status}: ${detail}`);
    error.status = response.status;
    throw error;
  }
}

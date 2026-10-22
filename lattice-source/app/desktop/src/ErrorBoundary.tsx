import { Component, ErrorInfo, ReactNode } from "react";

/** If any part of the interface throws while rendering, show a readable message with a way
 *  back, instead of React unmounting everything and leaving an empty (dark) window. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Lattice interface error:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="startup" role="alert">
        <p className="brand-name">Lattice</p>
        <p className="text-bad">
          Something went wrong in the assistant's screen. Your knowledge base and model are not affected.
        </p>
        <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>
          Reload
        </button>
        <p className="meta mono">{this.state.error.message}</p>
      </div>
    );
  }
}

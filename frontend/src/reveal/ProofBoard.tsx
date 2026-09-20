import type { ProofBoardModel } from "./proofBoardModel";

/**
 * Post-reveal proof board (Phase 18C) — four compact proof cards
 * (WHO / WHY / WEAPON / WHEN), each listing the supporting
 * discovered-evidence nodes (title + point) the case published.
 *
 * PURE DTO-driven: every node is server-supplied `explanation` text from a
 * validated RevealResponse; this component only groups/renders it. All text
 * goes through React's default string rendering (inert — no
 * dangerouslySetInnerHTML, no HTML construction). The connecting
 * lines/grouping are pure CSS — this is not a node editor, and nothing here
 * is interactive beyond the page's normal scroll.
 */
export default function ProofBoard({ model }: { model: ProofBoardModel }) {
  return (
    <div className="reveal-proof-board" data-testid="reveal-proof-board" aria-label="Proof board">
      <h3>Proof board</h3>
      <p className="reveal-proof-note">
        Each claim below is backed by evidence the case publishes after reveal —
        moving the accusation from guesswork to a provable chain.
      </p>
      <div className="reveal-proof-cards">
        {model.cards.map((card) => (
          <div className="reveal-proof-card" data-testid={`proof-card-${card.dimension.toLowerCase()}`} key={card.dimension}>
            <h4 className="reveal-proof-card-title" data-testid={`proof-card-${card.dimension.toLowerCase()}-title`}>
              {card.label}
            </h4>
            {card.nodes.length === 0 ? (
              <p className="reveal-proof-empty" data-testid={`proof-card-${card.dimension.toLowerCase()}-empty`}>
                No supporting evidence published.
              </p>
            ) : (
              <ul className="reveal-proof-nodes">
                {card.nodes.map((node, index) => (
                  <li
                    className="reveal-proof-node"
                    key={`${node.evidenceId}-${index}`}
                    data-testid={`proof-node-${card.dimension.toLowerCase()}-${index}`}
                  >
                    <strong>{node.title}</strong>
                    {node.point !== "" ? <span> — {node.point}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
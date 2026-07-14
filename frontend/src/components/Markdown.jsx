import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

// Replaces the hand-rolled regex renderer (which dropped **bold**, tables,
// blockquotes, etc.). react-markdown + remark-gfm covers the full GFM spec
// (bold/italic/strikethrough/tables/task-lists/autolinks); rehype-highlight
// adds syntax highlighting to fenced code blocks.
const MarkdownImpl = ({ children }) => {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
};

export default memo(MarkdownImpl);

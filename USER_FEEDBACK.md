# User Feedback for xAI Grok CLI / Chat Experience

**Date:** 2026-05-29
**User Context:** Using Grok via iOS (Termius app) for mobile, and desktop.

## Summary of Feedback

The user generally likes the Grok CLI experience **more than Copilot CLI on desktop**.

However, on **mobile (iOS with Termius)**, Copilot CLI is significantly easier to use for viewing and scrolling back through chat history.

### Specific Pain Points & Requests

1. **Visibility of Agent Output vs User Input**
   - Currently, the interface primarily shows the text input field and the user's own previous prompts.
   - The user already knows what they typed, so they care much more about seeing the **agent's (Grok's) output**.
   - They would like options/knobs to prioritize or expand the visibility of agent responses over their own previous messages.

2. **Mobile Touch Scroll Direction**
   - On touch devices, the current scroll behavior feels backwards to the user.
   - They would like a **setting to invert scroll direction** for mobile/touch interfaces.
   - Specifically: Sliding finger **up** should scroll the content **up** (more natural for them).
   - This is the opposite of many standard touch UIs.
   - Request: Add a toggle or preference for scroll direction inversion, especially important on mobile.

3. **UI/Scrolling Experience on Mobile**
   - Likes the idea of a scrollbar on the textarea.
   - Wants better overall scrollback and visibility of conversation history on small screens / terminal apps like Termius.
   - Desires more tunable options for the chat view (what is shown, how much history, focus on output).

### Additional Context
- User has been actively using the interface for development work (building and debugging a Python project).
- They appreciate the power of the CLI but find the mobile presentation and information density needs improvement compared to alternatives.

---

## Suggested Improvements (from user perspective)

- Add user-configurable display options (e.g., "Focus on agent output", "Show less of my previous prompts").
- Mobile-specific touch settings, including scroll direction inversion.
- Better history scrolling and visibility controls, especially in terminal-based clients on iOS.
- More "knobs" for tuning the chat presentation.

This feedback was captured directly during active usage of the Grok interface for software engineering tasks.
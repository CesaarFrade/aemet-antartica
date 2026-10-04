// The `/vitest` entry point registers the DOM matchers against Vitest's `expect`
// and augments its types at the same time. The bare `@testing-library/jest-dom`
// import only does the former for Jest's globals, which leaves every matcher call
// failing `tsc` with "Property 'toBeInTheDocument' does not exist".
import '@testing-library/jest-dom/vitest';

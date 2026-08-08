import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';

/**
 * Minimal re-implementation of eslint-plugin-react's `jsx-uses-vars`.
 * Core ESLint scope analysis does not count JSX identifiers as references,
 * so without this every component used only in JSX is a `no-unused-vars`
 * false positive. Marking the variable as used is all the real rule does —
 * inlining it avoids pulling in the whole eslint-plugin-react dependency
 * tree for one rule.
 */
const jsxRuntime = {
  rules: {
    'jsx-uses-vars': {
      meta: { type: 'problem', schema: [] },
      create(context) {
        return {
          JSXOpeningElement(node) {
            let name = node.name;
            if (name.type === 'JSXNamespacedName') return; // <svg:rect> — intrinsic
            if (name.type === 'JSXMemberExpression') {
              // <Foo.Bar /> / <motion.div /> — the in-scope variable is the
              // root object, regardless of its casing.
              while (name.type === 'JSXMemberExpression') name = name.object;
              context.sourceCode.markVariableAsUsed(name.name, node);
            } else if (/^[A-Z]/.test(name.name)) {
              // Bare lowercase tags (<div/>) are intrinsic elements, not vars.
              context.sourceCode.markVariableAsUsed(name.name, node);
            }
          },
        };
      },
    },
  },
};

/**
 * ESLint 9 flat config — correctness-focused, stylistic noise off.
 *
 * Policy: rules that catch real bugs (no-undef, rules-of-hooks, unused vars)
 * are enforced; purely stylistic churn is disabled rather than half-fixed
 * across a large existing codebase. Scope is src/ only — dist, e2e scripts
 * and the root verify-*.mjs harnesses are not linted.
 */
export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'e2e/**',
      'verify-shots/**',
      '*.mjs',
      '*.cjs',
    ],
  },
  {
    files: ['src/**/*.{js,jsx}', '*.config.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2021,
        // config files (vite/vitest/tailwind/postcss) run in node
        ...globals.node,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'jsx-runtime': jsxRuntime,
    },
    rules: {
      ...js.configs.recommended.rules,

      // React hooks correctness — the reason this plugin exists.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // JSX usage counts as a reference (see jsxRuntime above).
      'jsx-runtime/jsx-uses-vars': 'error',

      // Real-bug catchers.
      'no-undef': 'error',
      'no-unused-vars': [
        'warn',
        {
          // `_`-prefixed args/vars and ALL_CAPS module constants are
          // intentional; catch (_err) {} is a common deliberate pattern.
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],

      // Stylistic / noisy-on-legacy rules intentionally off.
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
];

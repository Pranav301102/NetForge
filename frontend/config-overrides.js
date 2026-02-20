const path = require('path');

// Stub out @copilotkitnext/web-inspector which fails to chunk under CRA webpack.
// It's a dev-only Lit-based inspector tool not needed for the app to function.
module.exports = function override(config) {
  config.resolve = config.resolve || {};
  config.resolve.alias = config.resolve.alias || {};
  config.resolve.alias['@copilotkitnext/web-inspector'] = path.resolve(
    __dirname,
    'src/stubs/web-inspector-stub.js'
  );
  return config;
};

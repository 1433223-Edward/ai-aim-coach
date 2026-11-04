const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../frontend");

// Load the real module graph in one context; only test roots add a lexical inspection export.
async function loadModule(name, context, rootSource) {
    const cache = new Map();
    function create(filename, override) {
        if (!cache.has(filename)) cache.set(filename, new vm.SourceTextModule(
            override ?? fs.readFileSync(filename, "utf8"), {context, identifier: filename,
                importModuleDynamically: async (specifier, parent) => {
                    const child = linker(specifier, parent);
                    if (child.status === "unlinked") await child.link(linker);
                    if (child.status === "linked") await child.evaluate();
                    return child;
                }}));
        return cache.get(filename);
    }
    const module = create(path.join(root, name), rootSource);
    function linker(specifier, parent) {
        const filename = path.resolve(path.dirname(parent.identifier), specifier);
        if (!filename.startsWith(root + path.sep)) throw new Error("Unexpected non-local module");
        return create(filename);
    }
    await module.link(linker);
    return module;
}
module.exports = {loadModule};

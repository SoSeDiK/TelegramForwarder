with import <nixpkgs>
{
  config.allowUnfree = true;
};

# nix-shell ./shell.nix
# 
# python -m venv venv
# source venv/bin/activate
# pip install -e .
# telegram-forwarder
stdenv.mkDerivation {
  name = "python";
  buildInputs = [
    python314
  ];
}

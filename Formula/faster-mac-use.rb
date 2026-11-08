class FasterMacUse < Formula
  include Language::Python::Virtualenv

  desc "Background-first, multi-cursor browser & macOS Accessibility engine for AI agents"
  homepage "https://lifan-builds.github.io/faster-mac-use/"
  url "https://github.com/lifan-builds/faster-mac-use/archive/refs/heads/main.tar.gz"
  version "0.1.0"
  license "MIT"
  head "https://github.com/lifan-builds/faster-mac-use.git", branch: "main"

  depends_on :macos
  depends_on "python@3.12"

  def install
    system "swiftc", "-O", "src/native/fmu_ax.swift", "-o", "bin/fmu-ax"
    bin.install "bin/fmu-ax"
    virtualenv_install_with_resources
  end

  test do
    assert_match "faster-mac-use", shell_output("#{bin}/fmu --version")
  end
end

# Importing the prepared repository into GitHub

The current automation connection can read existing repositories and commit to them, but cannot **create** an entirely new GitHub repository. No remote push has been performed.

**Preferred first stage: private GitHub repository.** The project is `AGPL-3.0-only` despite initially being private. Public release is a separate deliberate decision after provenance and tests are reviewed.

1. In GitHub, create an empty **private** repository named `Spiral_Cosmic_Conductor` under `RyoSpiralArchitect`. Do not initialize it with generated boilerplate; this local repository already has README and LICENSE.
2. Download `Spiral_Cosmic_Conductor.bundle` and use a terminal to restore the exact initial commit:

   ```bash
   git clone Spiral_Cosmic_Conductor.bundle Spiral_Cosmic_Conductor
   cd Spiral_Cosmic_Conductor
   git remote remove origin
   git remote add origin git@github.com:RyoSpiralArchitect/Spiral_Cosmic_Conductor.git
   git push -u origin main
   ```

   Alternatively, extract the source ZIP, `git init -b main`, commit locally and push the new repository.
3. Review GitHub Actions outcomes, compare them with local results and do not claim model-performance gains from scripted tests.

For source-only quick verification before pushing:

```bash
python3 -m unittest discover -s tests -v
python3 -m conductor cosmic-demo --out runs/sample
python3 -m conductor audit runs/sample
```

Nothing in this project requires a GitHub remote for local execution.

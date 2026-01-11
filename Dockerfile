FROM registry.suse.com/bci/python:3.13

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml pyproject.toml
COPY src/ src/

# Install dependencies using uv
RUN uv sync

EXPOSE 5000

CMD ["uv", "run", "gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "src.server:app"]
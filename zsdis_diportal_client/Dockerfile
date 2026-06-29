ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy addon files
COPY run.sh /
COPY *.py /app/

RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]

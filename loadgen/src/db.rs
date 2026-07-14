use std::path::Path;

use anyhow::{Context, Result};
use clap::ValueEnum;
use native_tls::{Certificate, TlsConnector};
use postgres_native_tls::MakeTlsConnector;
use serde::Serialize;
use tokio_postgres::{CancelToken, Client, Config, NoTls};

#[derive(Debug, Clone, Copy, ValueEnum, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum TlsMode {
    Disable,
    Require,
    VerifyFull,
}

fn tls_connector(mode: TlsMode, ca_cert: Option<&Path>) -> Result<MakeTlsConnector> {
    let mut builder = TlsConnector::builder();
    if mode == TlsMode::Require {
        builder.danger_accept_invalid_certs(true);
        builder.danger_accept_invalid_hostnames(true);
    }
    if let Some(path) = ca_cert {
        let pem = std::fs::read(path)
            .with_context(|| format!("failed to read CA certificate {}", path.display()))?;
        builder.add_root_certificate(Certificate::from_pem(&pem)?);
    }
    Ok(MakeTlsConnector::new(builder.build()?))
}

pub async fn connect(
    dsn: &str,
    mode: TlsMode,
    ca_cert: Option<&Path>,
    application_name: &str,
) -> Result<Client> {
    let mut config: Config = dsn.parse().context("invalid PostgreSQL DSN")?;
    config.application_name(application_name);

    match mode {
        TlsMode::Disable => {
            let (client, connection) = config.connect(NoTls).await?;
            tokio::spawn(async move {
                if let Err(error) = connection.await {
                    tracing::debug!(%error, "PostgreSQL connection ended");
                }
            });
            Ok(client)
        }
        TlsMode::Require | TlsMode::VerifyFull => {
            let connector = tls_connector(mode, ca_cert)?;
            let (client, connection) = config.connect(connector).await?;
            tokio::spawn(async move {
                if let Err(error) = connection.await {
                    tracing::debug!(%error, "PostgreSQL TLS connection ended");
                }
            });
            Ok(client)
        }
    }
}

pub async fn cancel(token: &CancelToken, mode: TlsMode, ca_cert: Option<&Path>) -> Result<()> {
    match mode {
        TlsMode::Disable => token.cancel_query(NoTls).await?,
        TlsMode::Require | TlsMode::VerifyFull => {
            token.cancel_query(tls_connector(mode, ca_cert)?).await?
        }
    }
    Ok(())
}

pub fn redact_dsn(dsn: &str) -> String {
    let Some(scheme_end) = dsn.find("://") else {
        return "configured".to_string();
    };
    let credentials_start = scheme_end + 3;
    let Some(at_offset) = dsn[credentials_start..].find('@') else {
        return dsn.to_string();
    };
    let at = credentials_start + at_offset;
    let credentials = &dsn[credentials_start..at];
    let user = credentials.split(':').next().unwrap_or(credentials);
    format!("{}{}@{}", &dsn[..credentials_start], user, &dsn[at + 1..])
}

#[cfg(test)]
mod tests {
    use super::redact_dsn;

    #[test]
    fn redacts_password_from_url_dsn() {
        assert_eq!(
            redact_dsn("postgresql://bench:secret@localhost:6432/bench"),
            "postgresql://bench@localhost:6432/bench"
        );
    }
}

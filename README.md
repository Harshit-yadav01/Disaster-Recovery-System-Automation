# Disaster-Recovery-System-Automation

🔹 Overview

This project provides a localhost automation tool to orchestrate disaster recovery (DR) workflows for workloads running on HPE Alletra MP storage and VMware SRM.
The tool integrates storage failover, VM recovery, application restart, and DNS redirection into a single button‑driven interface.


🔹 Architecture

Frontend: Localhost web app with buttons for Failover and Failback.

Backend Modules:

- Storage API (HPE Alletra MP)
- VM orchestration (VMware SRM)
- Application recovery (Ansible playbooks)
- Infrastructure updates (Terraform scripts)
- Monitoring: Real‑time dashboard with logs and compliance reporting.


🔹 Workflow Steps

Step 1: Storage Failover
-Trigger Alletra MP replication to activate DR volumes.
-Remount replicated datastores at DR site.
-Ensure data consistency (sync/async replication depending on site topology).

Step 2: VM Recovery
- VMware SRM executes recovery plans.
- DR ESXi hosts mount replicated datastores.
- VMs powered on in sequence: DB → App → Web.
- Dependencies enforced to avoid service mis‑ordering.

Step 3: Application Recovery
- Ansible playbooks restart DB, App, and Web services inside VMs.
- Health checks validate service readiness.
- Terraform updates DNS/load balancer to redirect traffic to DR site IPs.

Step 4: DNS & Reporting
- DNS TTL adjusted for faster propagation.
- Traffic redirected to DR site.
- Dashboard shows progress of each stage.
- Compliance report generated with RTO/RPO metrics, logs, and timestamps.


🔹 Failback Workflow

- Resync data from DR → Primary Alletra MP.
- Power down DR VMs in sequence.
- Remount datastores at primary site.
- Restart VMs and services at primary site.
- Update DNS back to primary IPs.
- Generate failback compliance report.


🔹 Features

- One‑click Failover and Failback.
- Role‑based access (Operator vs Admin).
- Snapshot‑based test recovery mode.
- Config drift detection between primary and DR.
- Real‑time monitoring and alerting.
- Automated compliance reporting.


🔹 Requirements

- HPE Alletra MP with replication configured.
- VMware SRM with recovery plans defined.
- Ansible installed with playbooks for DB/App/Web.
- Terraform configured for DNS/load balancer updates.
- Secure API keys stored in environment variables.